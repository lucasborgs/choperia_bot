from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Dono
    OWNER_PHONE: str  # Ex: "5511999998888"

    # WAHA
    WAHA_API_KEY: str
    WAHA_URL: str

    # Banco de dados
    DATABASE_URL: str

    # Groq (transcrição de áudio)
    GROQ_API_KEY: str

    # OpenAI (NLU)
    OPENAI_API_KEY: str

    @property
    def owner_jid(self) -> str:
        """JID do dono no formato esperado pelo WhatsApp."""
        return f"{self.OWNER_PHONE}@s.whatsapp.net"

    model_config = {"env_file": ".env"}


settings = Settings()
