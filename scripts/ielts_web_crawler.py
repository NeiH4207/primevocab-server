#!/usr/bin/env python3
"""
Enhanced IELTS Web Crawler
Downloads images locally and groups tasks by time periods
Based on crawler.py reference
"""

import concurrent.futures
import hashlib
import json
import os
import random
import re
import sys
import time
import threading
from datetime import datetime, UTC
from typing import Dict, List, Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup, Tag
from tqdm import tqdm

# Add the parent directory to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pymongo import MongoClient
from aiforen.modules.aws.s3 import upload_image_to_s3


class EnhancedIELTSWebCrawler:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
                "Accept-Encoding": "gzip, deflate",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
            }
        )

        # Create images directory
        self.images_dir = os.path.join(
            os.path.dirname(__file__), "..", "static", "images", "ielts_tasks"
        )
        os.makedirs(self.images_dir, exist_ok=True)

        # MongoDB connection
        self.db = self.connect_mongodb()
        
        # Thread-safe group cache and lock for group creation
        self._group_cache = {}
        self._group_lock = threading.Lock()
        self._task_counter_lock = threading.Lock()
        self._total_saved = 0

        # Rate limiting
        self.delay_range = (1, 3)

    def connect_mongodb(self):
        """Connect to MongoDB using environment variables"""
        try:
            from dotenv import load_dotenv

            load_dotenv()

            # Get connection details from environment
            mongo_uri = os.getenv(
                "MONGODB_URI",
                "mongodb+srv://aiforen_admin:aiforen123@aiforen.k9ll90b.mongodb.net/",
            )
            db_name = os.getenv(
                "MONGO_AIFOREN_DB_NAME", os.getenv("DB_NAME", "aiforen_db")
            )

            client = MongoClient(mongo_uri)
            db = client[db_name]

            # Test connection
            client.admin.command("ping")
            print(f"✅ MongoDB connection established to database: {db_name}")
            return db
        except Exception as e:
            print(f"❌ MongoDB connection failed: {e}")
            return None

    def remove_html_attributes(self, html_string):
        """Clean HTML by removing attributes, based on reference crawler"""
        # Remove span attributes and tags
        clean_html = re.sub(r"<span[^>]*>", "", html_string)
        clean_html = re.sub(r"</span[^>]*>", "", clean_html)

        # Remove other attributes but keep tags
        clean_html = re.sub(r"<p[^>]*>", "<p>", clean_html)
        clean_html = re.sub(r"<a[^>]*>", "<a>", clean_html)
        clean_html = re.sub(r"<meta[^>]*>", "", clean_html)
        clean_html = re.sub(r"<b[^>]*>", "", clean_html)

        # Remove style tags completely
        clean_html = re.sub(r"<style[^>]*>.*?</style>", "", clean_html, flags=re.DOTALL)

        # Fix quotes
        clean_html = clean_html.replace("'", '"')
        clean_html = clean_html.replace('"', "\u2019")

        return clean_html

    def download_image(self, image_url: str, task_id: str) -> Optional[str]:
        """Download image and return local filename"""
        try:
            if not image_url or image_url == "null":
                return None

            # Make URL absolute
            if image_url.startswith("/"):
                image_url = "https://ieltsonlinetests.com" + image_url

            # Generate unique filename
            url_hash = hashlib.md5(image_url.encode()).hexdigest()[:8]
            file_extension = os.path.splitext(urlparse(image_url).path)[1] or ".jpg"
            filename = f"{task_id}_{url_hash}{file_extension}"
            filepath = os.path.join(self.images_dir, filename)

            # Skip if already downloaded
            if os.path.exists(filepath):
                return filename

            # Download image
            response = self.session.get(image_url, timeout=30)
            response.raise_for_status()

            with open(filepath, "wb") as f:
                f.write(response.content)

            print(f"📸 Downloaded image: {filename}")
            return filename

        except Exception as e:
            print(f"⚠️ Failed to download image {image_url}: {e}")
            return None

    def get_page_content(self, url: str) -> Optional[str]:
        """Get HTML content of a page with error handling"""
        try:
            time.sleep(random.uniform(*self.delay_range))
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            return response.text
        except Exception as e:
            print(f"❌ Error fetching {url}: {e}")
            return None

    def parse_main_page_tasks(self, html_content: str) -> List[Dict]:
        """Parse writing tasks from main page"""
        soup = BeautifulSoup(html_content, "html.parser")
        tasks = []

        # Find all button elements containing writing task data
        buttons = soup.find_all(
            "button", class_="practice-item__btn mocktest-card__pack -writing"
        )

        for button in buttons:
            try:
                if not isinstance(button, Tag):
                    continue
                parent = button.find_parent("div", class_="mocktest-card__infos")
                if not parent or not isinstance(parent, Tag):
                    continue

                group_name = parent.find("h2", class_="mocktest-card__title")
                if not group_name:
                    continue

                title = button.find("h6", class_="mocktest-card__pack-title")
                taken_count = button.find("div", class_="mocktest-card__pack-taken")

                task_data = {
                    "group_name": group_name.text.strip(),
                    "title": title.text.strip() if title else "Unknown Title",
                    "taken_count": taken_count.text.strip() if taken_count else "0",
                    "data_href": button.get("data-href", ""),
                    "data_url_simulation_mode": button.get(
                        "data-url-simulation-mode", ""
                    ),
                }

                tasks.append(task_data)
            except Exception as e:
                print(f"⚠️ Error parsing task button: {e}")
                continue

        return tasks

    def save_individual_task_to_mongodb(self, task_data: Dict) -> bool:
        """Save a single task to MongoDB immediately after processing (thread-safe)"""
        if self.db is None:
            print("❌ Database connection not available")
            return False

        try:
            # Get or create group for this task (thread-safe)
            group_name = task_data["group_name"]
            group_id = self.get_or_create_group_threadsafe(group_name)
            
            if not group_id:
                print(f"⚠️ Could not create/find group for task: {task_data['title']}")
                return False

            # Add group_id to task
            task_data["group_id"] = group_id

            # Check if task already exists
            existing_task = self.db.writing_tasks.find_one(
                {"title": task_data["title"]}
            )

            if existing_task:
                print(f"⚠️ Task already exists: {task_data['title']}")
                return False

            # Generate unique task_id
            task_data["task_id"] = (
                f"crawled_{hashlib.md5(task_data['title'].encode()).hexdigest()[:12]}"
            )

            # Insert task immediately
            self.db.writing_tasks.insert_one(task_data)
            
            # Thread-safe counter increment
            with self._task_counter_lock:
                self._total_saved += 1
                
            print(f"✅ Saved task to MongoDB: {task_data['title']}")
            return True

        except Exception as e:
            print(f"❌ Error saving task {task_data.get('title', 'Unknown')}: {e}")
            return False

    def get_or_create_group_threadsafe(self, group_name: str) -> Optional[str]:
        """Get existing group or create new one and return group ID (thread-safe)"""
        if self.db is None:
            return None

        # Check cache first (thread-safe read)
        if group_name in self._group_cache:
            return self._group_cache[group_name]

        # Use lock for group creation to prevent race conditions
        with self._group_lock:
            # Double-check cache after acquiring lock
            if group_name in self._group_cache:
                return self._group_cache[group_name]

            try:
                # Use upsert to handle concurrent creation attempts
                group_data = {
                    "name": group_name,
                    "description": f"IELTS Writing Practice tasks for {group_name.split()[-1]}",
                    "difficulty": "intermediate",
                    "estimated_time": 60,
                    "created_at": datetime.now(UTC),
                    "updated_at": datetime.now(UTC),
                    "is_active": True,
                }

                # Use upsert to avoid duplicate key errors
                result = self.db.writing_task_groups.update_one(
                    {"name": group_name},
                    {"$setOnInsert": group_data},
                    upsert=True
                )

                # Get the group to obtain the ID
                group = self.db.writing_task_groups.find_one({"name": group_name})
                if group:
                    group_id = str(group["_id"])
                    self._group_cache[group_name] = group_id
                    
                    if result.upserted_id:
                        print(f"📁 Created new group: {group_name}")
                    else:
                        print(f"📁 Using existing group: {group_name}")
                        
                    return group_id

            except Exception as e:
                print(f"❌ Error creating group {group_name}: {e}")
                return None

        return None

    def extract_task_details_threadsafe(self, base_url: str, task_info: Dict) -> int:
        """Extract detailed content from individual task pages and save immediately (thread-safe)"""
        # Fix URL construction - data_href already contains full path
        data_href = task_info["data_href"]
        if data_href.startswith("http"):
            task_url = f"{data_href}/test?mode=practice_test&parts=full&duration=60"
        else:
            task_url = (
                f"{base_url}{data_href}/test?mode=practice_test&parts=full&duration=60"
            )
            
        html_content = self.get_page_content(task_url)

        if not html_content:
            return 0

        soup = BeautifulSoup(html_content, "html.parser")
        sections = soup.find_all("section", class_="test-contents ckeditor-wrapper")

        saved_count = 0

        for section in sections:
            try:
                # Get task type and title
                if not isinstance(section, Tag):
                    continue
                title_elem = section.find("h1", class_="test-contents__title")
                if not title_elem:
                    continue

                task_type = title_elem.text.strip()

                # Get description
                paragraphs = section.find_all("p")
                description_parts = []
                for p in paragraphs:
                    if p.text.strip():
                        description_parts.append(str(p))

                description = "".join(description_parts) + "<br>"
                description = self.remove_html_attributes(description)

                # Get image and upload to S3
                image_elem = section.find("img", class_="test-contents__img-custom")
                image_url = None
                s3_key = None

                if image_elem and isinstance(image_elem, Tag):
                    image_url = str(image_elem.get("src", ""))
                    if image_url and "/sites" in image_url:
                        image_url = "https://ieltsonlinetests.com" + image_url

                    # Generate task ID for image
                    task_id = hashlib.md5(
                        f"{task_info['title']}_{task_type}".encode()
                    ).hexdigest()[:12]

                    # Upload image to S3
                    if image_url:
                        s3_key = upload_image_to_s3(image_url, task_id, "ielts_tasks")
                        if s3_key:
                            print(f"☁️ Image uploaded to S3: {s3_key}")
                        else:
                            print(f"⚠️ Failed to upload image to S3: {image_url}")

                # Determine task properties
                task_number = 1 if "Task 1" in task_type else 2

                task_data = {
                    "title": f"{task_info['title']} - {task_type}",
                    "description": description,
                    "task_type": f"task_{task_number}",
                    "difficulty": "intermediate",
                    "estimated_time": 20 if task_number == 1 else 40,
                    "word_limit": 150 if task_number == 1 else 250,
                    "source": "ieltsonlinetests.com",
                    "original_image_url": image_url,
                    "s3_key": s3_key,
                    "is_active": True,
                    "created_at": datetime.now(UTC),
                    "updated_at": datetime.now(UTC),
                    "source_url": task_url,
                    "group_name": task_info["group_name"],
                    "upload_status": "completed" if s3_key else "failed",
                    "required_plan": "free",
                }

                # Save task immediately after processing (thread-safe)
                if self.save_individual_task_to_mongodb(task_data):
                    saved_count += 1

            except Exception as e:
                print(f"⚠️ Error extracting task details: {e}")
                continue

        return saved_count

    def crawl_ielts_tasks(self, pages: List[int] = [0, 1, 2]):
        """Main crawling function with parallel processing and immediate saving"""
        print("🚀 Starting Enhanced IELTS Web Crawler with parallel processing...")

        if self.db is None:
            print("❌ MongoDB connection failed. Exiting.")
            return

        self._total_saved = 0
        base_url = "https://ieltsonlinetests.com"
        categories = ["academic", "general"]
        category = categories[0]

        for page in pages:
            print(f"\n📄 Processing page {page}...")
            main_url = f"{base_url}/ielts-exam-library?tab={category}&search&sort&skill=writing&page={page}"

            # Get main page content
            html_content = self.get_page_content(main_url)
            if not html_content:
                print(f"❌ Failed to get content for page {page}")
                continue

            # Parse task list
            task_list = self.parse_main_page_tasks(html_content)
            print(f"📝 Found {len(task_list)} task groups on page {page}")

            # Extract detailed content with parallel processing and immediate saving
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(task_list))) as executor:
                future_to_task = {
                    executor.submit(
                        self.extract_task_details_threadsafe, base_url, task_info
                    ): task_info
                    for task_info in task_list
                }

                for future in tqdm(
                    concurrent.futures.as_completed(future_to_task),
                    total=len(future_to_task),
                    desc=f"Processing page {page}",
                ):
                    task_info = future_to_task[future]
                    try:
                        saved_count = future.result()
                        if saved_count > 0:
                            print(f"📝 Saved {saved_count} tasks from group: {task_info['title']}")
                    except Exception as exc:
                        print(f'❌ Error processing {task_info["title"]}: {exc}')

        print(f"\n🎉 Total tasks saved to MongoDB: {self._total_saved}")


def main():
    crawler = EnhancedIELTSWebCrawler()
    # Process pages one by one, saving tasks immediately
    crawler.crawl_ielts_tasks(pages=[0, 1, 2, 3, 4])


if __name__ == "__main__":
    main()
