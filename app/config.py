from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    openai_api_key: str
    chroma_host: str = "localhost"
    chroma_port: int = 8000
    # "embedded" uses in-process ChromaDB for local dev without Docker
    # "server" connects to a running ChromaDB HTTP server (used in Docker)
    chroma_mode: str = "server"
    collection_name: str = "packaging_kb"
    data_dir: str = "data"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
