# xGenius Streamlit Deployment

Use these settings in Streamlit Community Cloud:

- Repository: `Nickesmi/football_predictor_AI-master`
- Branch: `tennis-prediction-engine`
- Main file path: `streamlit_app.py`

Add these values in App settings -> Secrets:

```toml
APIFOOTBALL_API_KEY = "your_api_football_key"
API_FOOTBALL_KEY = "your_api_football_key"
APIFOOTBALL_HOST = "v3.football.api-sports.io"

RAPIDAPI_KEY = "your_rapidapi_key"
RAPIDAPI_HOST = "sofascore.p.rapidapi.com"

FOOTBALL_PREDICTOR_DB_PATH = "data/engine.db"
```

`main.py` is still safe as a fallback entrypoint, but `streamlit_app.py` is the recommended Streamlit app file.
