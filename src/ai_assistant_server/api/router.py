from fastapi import APIRouter, Depends

from ai_assistant_server.api.dependencies import verify_origin
from ai_assistant_server.api.routes import auth, chat, conversations, task_lists, tasks

# verify_origin runs on every API request (CSRF check for cookie auth)
api_router = APIRouter(prefix="/api/v1", dependencies=[Depends(verify_origin)])
api_router.include_router(auth.router)
api_router.include_router(chat.router)
api_router.include_router(conversations.router)
api_router.include_router(tasks.router)
api_router.include_router(task_lists.router)
