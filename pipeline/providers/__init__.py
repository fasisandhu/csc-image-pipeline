"""Generator adapters. Each exposes generate(prompt, refs, size, aspect) -> GenerateResult."""
from .base import GenerateResult, Provider, ProviderError


def get_provider(tier: str) -> Provider:
    """Map a shot tier to its adapter. The only place tiers meet concrete providers."""
    if tier == "draft":
        from .cloudflare import CloudflareProvider
        return CloudflareProvider()
    if tier in ("standard", "premium"):
        from .gemini import GeminiProvider
        return GeminiProvider(tier)
    raise ProviderError(f"unknown tier {tier!r}")


__all__ = ["GenerateResult", "Provider", "ProviderError", "get_provider"]
