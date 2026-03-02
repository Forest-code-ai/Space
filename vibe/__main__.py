def main() -> None:
    import uvicorn

    uvicorn.run(
        "vibe.main:app",
        host="127.0.0.1",
        port=8000,
        reload=False,
    )


if __name__ == "__main__":
    main()
