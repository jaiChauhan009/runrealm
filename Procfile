web: gunicorn main:app -w 2 -k uvicorn.workers.UvicornWorker --host 0.0.0.0 --port $PORT --timeout 120 --graceful-timeout 30
