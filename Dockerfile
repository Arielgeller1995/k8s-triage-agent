# Use the official slim Python 3.11 image as the base.
# "slim" strips out docs, tests, and other extras to keep the image small.
FROM python:3.11-slim

# Set the working directory inside the container.
# All subsequent COPY and RUN commands resolve paths relative to this.
WORKDIR /app

# Copy only the requirements file first — before the rest of the source.
# Docker caches each layer. By copying requirements.txt alone, the pip install
# layer is only re-run when requirements.txt changes, not on every code edit.
COPY requirements.txt .

# Install dependencies with no cache dir to avoid storing pip's download
# cache in the image, keeping the layer size smaller.
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application source files into the container.
# This happens after pip install so that code changes don't bust the
# cached dependency layer above.
COPY main.py agent.py ./

# Tell Docker (and humans) that the container listens on port 8000.
# This is documentation — it does not actually publish the port; that
# happens at runtime with `docker run -p 8000:8000`.
EXPOSE 8000

# Start the app with uvicorn when the container runs.
# --host 0.0.0.0  → listen on all interfaces (not just localhost), required
#                   inside a container so external traffic can reach it.
# --port 8000     → match the EXPOSE instruction above.
# main:app        → the `app` object inside main.py.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
