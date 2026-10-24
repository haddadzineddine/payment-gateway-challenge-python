import uvicorn

from config import Settings

HOST = "0.0.0.0"
PORT = 8000


def main():
    url = f"http://localhost:{PORT}"
    print(f"Server:       {url}")

    if Settings().docs_enabled:
        print(f"Swagger docs: {url}/docs")

    uvicorn.run(
        app="app:create_app",
        factory=True,
        host=HOST,
        port=PORT,
        reload=True,
        workers=1,
    )


if __name__ == "__main__":
    main()
