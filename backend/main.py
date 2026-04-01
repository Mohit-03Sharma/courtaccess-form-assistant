import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from routers.assistant import router as assistant_router
from routers.voice_rest import router as voice_rest_router

load_dotenv()

app = FastAPI(title="CourtAccess Prototype")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(assistant_router, prefix="/api")
app.include_router(voice_rest_router, prefix="/api")

@app.get("/")
def health():
    return {"status": "ok"}