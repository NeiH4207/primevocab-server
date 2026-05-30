# AIFOREN API Documentation

This document provides an overview of the AIFOREN API endpoints, including authentication, writing tasks, and assessments.

## Base URL

All API endpoints are prefixed with the following base URL:

`/api/v1`

## Authentication

Authentication is handled via Google OAuth2. The following endpoints are used to manage the authentication flow.

### `GET /login-as`

Redirects the user to the Google OAuth2 consent screen to initiate the login process.

-   **Query Parameters**:
    -   `provider` (string, required): The OAuth provider. Currently, only `"google"` is supported.
    -   `origin` (string, required): The frontend URL to redirect back to after a successful login.

### `GET /auth/callback`

Handles the callback from Google after the user has granted consent. This endpoint is not called directly by the frontend.

### `GET /me`

Retrieves the profile of the currently authenticated user.

-   **Headers**:
    -   `Authorization: Bearer <access_token>` (required): The JWT access token obtained after a successful login.

## Writing API

The Writing API provides endpoints for fetching writing tasks, submitting answers, and retrieving evaluations.

### `GET /writing/task-groups`

Retrieves a list of available writing task groups.

-   **Response**:
    ```json
    {
      "status": "success",
      "data": [
        {
          "id": 1,
          "name": "IELTS Academic Writing",
          "total_tasks": 15
        }
      ]
    }
    ```

### `GET /writing/tasks`

Retrieves a list of writing tasks for a specific group.

-   **Query Parameters**:
    -   `group_id` (integer, required): The ID of the task group.
    -   `group_name` (string, required): The name of the task group.

### `GET /writing/tasks/{task_id}`

Retrieves a specific writing task by its ID.

-   **Path Parameters**:
    -   `task_id` (string, required): The ID of the task.

### `POST /writing/assessments`

Submits a user's answer for a writing task.

-   **Request Body**:
    ```json
    {
      "task_id": "string",
      "answer": "string"
    }
    ```

### `GET /writing/assessments`

Retrieves the assessment history for a specific task or user.

### `GET /writing/personal-tasks`

Retrieves the personal writing tasks created by the authenticated user.

### `POST /writing/personal-tasks`

Creates a new personal writing task for the authenticated user.

-   **Request Body**: (multipart/form-data)
    -   `task_type`: "Writing Task 1" or "Writing Task 2"
    -   `title`: The title of the task.
    -   `description`: The description of the task.
    -   `image`: (optional) An image file for the task. 