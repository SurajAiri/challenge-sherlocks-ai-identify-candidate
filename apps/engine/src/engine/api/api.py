from fastapi import FastAPI

api = FastAPI(
    title="Sherlocks AI Candidate Identify",
    description="Sherlocks AI Candidate Identify API",
    version="0.1.0",
)


@api.get("/health", tags=["Health"])
def health_check():
    return {"status": "healthy"}
