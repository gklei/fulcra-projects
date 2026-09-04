"""Vercel serverless entry point for the Build Chemistry FastAPI app."""

from app.web import app

__all__ = ["app"]