import uvicorn


def main() -> None:
    uvicorn.run("codeatlas.app:app", host="127.0.0.1", port=8010, workers=1)


if __name__ == "__main__":
    main()

