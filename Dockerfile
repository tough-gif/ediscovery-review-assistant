# Use an official Python runtime as a parent image
FROM python:3.11-slim

# Set working directory inside the container
WORKDIR /app


# Copy the local workspace files to the container
COPY . /app

# Install uv for fast, reliable package builds
RUN pip install --no-cache-dir uv

# Build and install the local package and its dependencies in the system environment
RUN uv pip install --system --no-cache .

# Expose Streamlit's default port
EXPOSE 8080

# Configure Streamlit behavior (disable browser auto-opening, set logs)
ENV STREAMLIT_SERVER_PORT=8080
ENV STREAMLIT_SERVER_ADDRESS=0.0.0.0

# Start Streamlit application
CMD ["streamlit", "run", "streamlit_app.py"]
