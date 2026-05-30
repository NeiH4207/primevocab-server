import concurrent.futures
import json
import re

import requests
from bs4 import BeautifulSoup
from tqdm import tqdm


# Function to get the HTML content of a page
def get_page_content(url):
    response = requests.get(url)
    response.raise_for_status()
    return response.text


# Function to parse writing task data from the HTML content
def parse_writing_task_data(html_content):
    soup = BeautifulSoup(html_content, "html.parser")
    tasks = []

    # Find all button elements containing writing task data
    buttons = soup.find_all(
        "button",
        class_="practice-item__btn mocktest-card__pack -writing",
        recursive=True,
    )

    for button in buttons:
        parent = button.find_parent("div", class_="mocktest-card__infos")
        group_name = parent.find("h2", class_="mocktest-card__title").text
        task_data = {
            "group-name": group_name,
            "title": button.find("h6", class_="mocktest-card__pack-title").text,
            "taken_count": button.find("div", class_="mocktest-card__pack-taken").text,
            "data_href": button["data-href"],
            "data_url_simulation_mode": button["data-url-simulation-mode"],
        }

        tasks.append(task_data)

    return tasks


def remove_span_attributes(html_string):
    # Regular expression to match <span ...> and replace it with <span>
    clean_span_string = re.sub(r"<span[^>]*>", "<span>", html_string)
    return clean_span_string


def remove_p_attributes(html_string):
    # Regular expression to match <span ...> and replace it with <span>
    clean_span_string = re.sub(r"<p[^>]*>", "<p>", html_string)
    return clean_span_string


def remove_a_attributes(html_string):
    # Regular expression to match <span ...> and replace it with <span>
    clean_span_string = re.sub(r"<a[^>]*>", "<a>", html_string)
    return clean_span_string


def remove_meta_attributes(html_string):
    # Regular expression to match <span ...> and replace it with <span>
    clean_span_string = re.sub(r"<meta[^>]*>", "", html_string)
    return clean_span_string


def remove_b_attributes(html_string):
    # Regular expression to match <span ...> and replace it with <span>
    clean_span_string = re.sub(r"<b[^>]*>", "", html_string)
    return clean_span_string


def remove_style_tags(html_string):
    # Regular expression to match <style ...> and replace it with '', similiarly for </style>
    clean_span_string = re.sub(r"<style[^>]*>", "", html_string)
    clean_span_string = re.sub(r"</style[^>]*>", "", clean_span_string)
    return clean_span_string


def remove_span_tags(html_string):
    # Regular expression to match <span ...> and replace it with '', similiarly for </span>
    clean_span_string = re.sub(r"<span[^>]*>", "", html_string)
    clean_span_string = re.sub(r"</span[^>]*>", "", clean_span_string)
    return clean_span_string


def remove_attributes(html_string):
    clean_span_string = remove_span_tags(html_string)
    clean_p_string = remove_p_attributes(clean_span_string)
    clean_a_string = remove_a_attributes(clean_p_string)
    clean_meta_string = remove_meta_attributes(clean_a_string)
    clean_b_string = remove_b_attributes(clean_meta_string)
    clean_style_string = remove_style_tags(clean_b_string)
    clean_comma_string = clean_style_string.replace("'", '"')
    final_string = clean_comma_string.replace('"', "\u2019")
    return final_string


# Function to extract detailed writing task content from a specific task page
def extract_task_content(url):
    _url = f"{url}/test?mode=practice_test&parts=full&duration=60"
    html_content = get_page_content(_url)
    soup = BeautifulSoup(html_content, "html.parser")
    sections = soup.find_all("section", class_="test-contents ckeditor-wrapper")

    tasks = []

    for section in sections:
        task_type = section.find_all("h1", class_="test-contents__title")[0].text
        description = (
            "".join([str(p) for p in section.find_all("p") if p.text != ""]) + "<br>"
        )

        description = remove_attributes(description)

        if "Task 1" in task_type:
            try:
                image_url = section.find("img", class_="test-contents__img-custom")[
                    "src"
                ]
            except:
                image_url = "null"
        else:
            try:
                image_url = section.find("img", class_="test-contents__img-custom")[
                    "src"
                ]
            except:
                image_url = "null"

        if "/sites" in image_url:
            image_url = "https://ieltsonlinetests.com" + image_url

        image_name = image_url.split("/")[-1]

        task_content = {
            "task-type": task_type,
            "description": description,
            "image": image_name,
        }

        tasks.append(task_content)

    return tasks


# Main function to get all writing task data
def get_all_writing_tasks(main_url):
    main_page_content = get_page_content(main_url)
    writing_tasks = parse_writing_task_data(main_page_content)
    all_tasks = []

    for task in tqdm(writing_tasks):
        task_content_url = f"https://ieltsonlinetests.com{task['data_href']}"
        tasks = extract_task_content(task_content_url)
        for sub_task in tasks:
            task_details = {
                "group-name": task["group-name"],
                "title": task["title"],
                "task-type": sub_task["task-type"],
                "description": sub_task["description"].replace('"', '"'),
                "image": sub_task["image"],
            }
            all_tasks.append(task_details)

    return all_tasks


def get_all_writing_tasks_parallel(main_url):
    main_page_content = get_page_content(main_url)
    writing_tasks = parse_writing_task_data(main_page_content)
    all_tasks = []

    with concurrent.futures.ThreadPoolExecutor() as executor:
        future_to_url = {
            executor.submit(
                extract_task_content, f"https://ieltsonlinetests.com{task['data_href']}"
            ): task
            for task in writing_tasks
        }
        for future in concurrent.futures.as_completed(future_to_url):
            task = future_to_url[future]
            try:
                tasks = future.result()
                for sub_task in tasks:
                    task_details = {
                        "group-name": task["group-name"],
                        "title": task["title"],
                        "task-type": sub_task["task-type"],
                        "description": sub_task["description"].replace('"', '"'),
                        "image": sub_task["image"],
                    }
                    all_tasks.append(task_details)
            except Exception as exc:
                print(f"Generated an exception: {exc}")

    return all_tasks


# URL of the main page containing writing tasks
pages = [0, 1, 2]
for page in pages:
    main_url = (
        "https://ieltsonlinetests.com/ielts-exam-library?skill=writing&page="
        + str(page)
    )

    # Get all writing tasks
    writing_tasks = get_all_writing_tasks_parallel(main_url)

    # Print the writing tasks as JSON
    print(json.dumps(writing_tasks, indent=4))

    # Save the writing tasks to a JSON file
    file_name = f"writing_tasks_page_{page}.json"
    with open(file_name, "w") as f:
        json.dump(writing_tasks, f, indent=4)
