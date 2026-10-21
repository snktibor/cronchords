FROM python:3.14-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --root-user-action=ignore -r requirements.txt
COPY cronchords cronchords

# The worker keeps nothing on disk, so it doesn't need root.
USER nobody

ENTRYPOINT ["python", "-m", "cronchords"]
CMD ["--every", "60"]
