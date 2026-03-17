FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
ENV SRE_DEFAULT_TOOL_TRANSPORT=mcp
ENV DATABASE_URL=sqlite:////app/ops.db

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir -r /app/requirements.txt

COPY . /app

RUN chmod +x /app/scripts/start_stack.sh

EXPOSE 8090 8501

CMD ["/app/scripts/start_stack.sh"]
