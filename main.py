from fastapi import FastAPI

from app.api import auth

app = FastAPI()

app.include_router(auth.router)

@app.get("/")
async def root():
    return {"message": "Hello World"}

@app.get("/healthz")
async def healthz():
    return {"status": "ok"}
