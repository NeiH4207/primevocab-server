import json
from multiprocessing import Pool

import requests
from tqdm import tqdm

url = "http://localhost:4207/api/v1/create_writing_question/eyJhbGciOiJSUzI1NiIsImtpZCI6IjY3NGRiYmE4ZmFlZTY5YWNhZTFiYzFiZTE5MDQ1MzY3OGY0NzI4MDMiLCJ0eXAiOiJKV1QifQ.eyJpc3MiOiJodHRwczovL2FjY291bnRzLmdvb2dsZS5jb20iLCJhenAiOiI2MzM2NzMzMTA1OS1uaTRnZGZianZ0c2pkdDY1MjFycmw1bmVidnFuZmI4ZC5hcHBzLmdvb2dsZXVzZXJjb250ZW50LmNvbSIsImF1ZCI6IjYzMzY3MzMxMDU5LW5pNGdkZmJqdnRzamR0NjUyMXJybDVuZWJ2cW5mYjhkLmFwcHMuZ29vZ2xldXNlcmNvbnRlbnQuY29tIiwic3ViIjoiMTE3NTkzNTc1MDQ1MzU1ODY4MTgzIiwiZW1haWwiOiJuZWloNDIwN0BnbWFpbC5jb20iLCJlbWFpbF92ZXJpZmllZCI6dHJ1ZSwibmJmIjoxNzE3OTI3NzQwLCJuYW1lIjoiVsWpIFF14buRYyBIaeG7g24iLCJwaWN0dXJlIjoiaHR0cHM6Ly9saDMuZ29vZ2xldXNlcmNvbnRlbnQuY29tL2EvQUNnOG9jSkdXOUFfYzREdXdiSlpadmRZNDBIOFFLVlZlMl9yMy1nRFJGQWNQczNFNE4xa1UybU49czk2LWMiLCJnaXZlbl9uYW1lIjoiVsWpIiwiZmFtaWx5X25hbWUiOiJRdeG7kWMgSGnhu4NuIiwiaWF0IjoxNzE3OTI4MDQwLCJleHAiOjE3MTc5MzE2NDAsImp0aSI6IjVmOTU1NDExYmE3OTRmNDdlYzlkMmY4NWIyYjY4ZTIzNjIzZDIzZTAifQ.neHshjJzKsQ2iKuyEtAkDgUcTjwJmTCdupmXAMiQlf90JV8X1h4rasXWdd-_3sL4YxATmMlw2knPpPB-rq0BkUi1GDjD3FNPTbyFuC4-wvFZTj3PcCgItRbX89gkhuTRxQ7zP3r93Gb8UUAKaAn_noumm0qr-nWgz6xSI_dVhdRF-FGvYVJ9EENwvkpv3s9WIqScFCp-dumf0_Tgvy8ZCKLQyp1aGftuubnyA8SPviGv0vjUxGJ4Okgo9WS6jBlbMBXwSP596Oeql5VtWvSr5tH-15cejdxqWq-P_DqVhg1pu-Xzq47hmQS_eEKfC7v_CxRmME6KCXodpZsjdH14gA"


def process_task(task):
    body_data = {
        "group": task["group-name"],
        "title": task["title"],
        "taskType": task["task-type"],
        "description": task["description"],
        "image": task["image"],
    }

    response = requests.post(url, params=body_data)
    return response.status_code


def process_file(file_name):
    with open(file_name) as f:
        writing_tasks = json.load(f)

    print(f"Pushing writing tasks from {file_name}")
    with Pool() as pool:
        results = list(
            tqdm(pool.imap(process_task, writing_tasks), total=len(writing_tasks))
        )

    return results


if __name__ == "__main__":
    pages = [0, 1, 2]

    for page in pages:
        file_name = f"writing_tasks_page_{page}.json"
        results = process_file(file_name)
        print(
            f"Completed {file_name}. Successful requests: {results.count(200)}/{len(results)}"
        )
