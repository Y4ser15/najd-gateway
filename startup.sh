#!/bin/bash
gunicorn main:app -k uvicorn.workers.UvicornWorker -c gunicorn_conf.py