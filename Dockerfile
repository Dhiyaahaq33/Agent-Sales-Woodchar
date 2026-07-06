FROM python:3.11-slim

WORKDIR /app

# Install OS deps for chromadb + playwright
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# Skip heavy optional scrapers for Docker (social media, playwright)
RUN pip install --no-cache-dir \
    apscheduler python-dotenv google-genai groq \
    google-auth-oauthlib google-api-python-client gspread \
    requests beautifulsoup4 lxml fake-useragent \
    reportlab scikit-learn pandas numpy \
    streamlit plotly chromadb pytz flask pyshorteners

COPY . .

# Create runtime dirs
RUN mkdir -p db logs data proposals dashboard

# Run migration then start
CMD ["sh", "-c", "python -m db.migrate_v31 && python main.py"]
