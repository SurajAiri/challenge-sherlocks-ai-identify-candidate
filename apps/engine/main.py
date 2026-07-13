from engine import api

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(api, host="0.0.0.0", port=8090, log_level="info")
