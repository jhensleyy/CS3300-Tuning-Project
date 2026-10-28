#imports CORSMiddleware to allow React to communicate w FastAPI
from fastapi.middleware.cors import CORSMiddleware

#Imports FastAPI to create the backend server
from fastapi import FastAPI

#imports BaseModel and FIeld to store and validate user inputs
from pydantic import BaseModel, Field

#import literal to limit the allowed fuel types
from typing import Literal

#Creates the backend server
app = FastAPI()

#allows React to send requests to the Python backend
app.add_middleware(
    CORSMiddleware, 

    #addresses allowed to communiccate with the backend
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173"
    ],

    #alllows React to send POST requests
    allow_methods=["POST"],

    #allows React to send data in JSON format
    allow_headers=["Content-Type"]
)

#stores and checks engine settings
class EngineSettings(BaseModel):
    elevation: float = Field(ge=-400, le=8000)
    idleRPM: int = Field(ge=500, le=1000)
    redLineRPM: int = Field(ge=5000, le=10000)
    horsepower: float = Field(ge=1)
    boostPSI: float = Field(le=45)
    fuelType: Literal["91","93","E85"]

#checks if the backend is running
@app.get("/")
def home():
    return {"message": "Backend is running"}

#receives engine settings
@app.post("/simulate")
def simulate(settings: EngineSettings):
    return {
        "message": "Engine settings received",
        "settings": settings.model_dump()
        }