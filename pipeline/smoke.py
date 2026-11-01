"""Step 2 smoke test: one call per adapter path, stop on first failure.

    python -m pipeline.smoke            # cloudflare text, cloudflare with ref, gemini standard 1K with ref
    python -m pipeline.smoke cloudflare # free calls only
"""
from __future__ import annotations

import sys

from dotenv import load_dotenv

from . import ledger
from .generate import run
from .providers import ProviderError

DRAFT_PROMPT = (
    '{"subject": "product packshot of a single hoodie", '
    '"garment": "heavyweight 450 GSM cotton fleece pullover hoodie, washed black, double-layer hood, '
    'flat drawcords with metal aglets, kangaroo pocket, 2x2 rib cuffs and hem", '
    '"decoration": "plain, unbranded", "scene": "seamless light grey studio background", '
    '"camera": "front view, straight on, 50mm, full garment in frame", '
    '"lighting": "soft even studio light", '
    '"style": "clean editorial streetwear catalogue, true-to-life colour, soft natural contrast, no film grain"}'
)
REF_PROMPT = "Image 0 is the layout reference. Same hoodie and framing, rotated to a three-quarter view."
FINAL_PROMPT = (
    "Front-view product packshot of a heavyweight 450 GSM cotton-rich fleece pullover hoodie in washed black, "
    "laid out as a ghost-mannequin catalogue shot. The fabric is thick and stands away from the body, with a "
    "double-layer hood holding its shape, flat drawcords with metal aglets through metal eyelets, a kangaroo "
    "pocket attached at the hem, wide 2x2 rib cuffs and hem, and coverstitched seams. Plain and unbranded. "
    "Straight-on camera, 50mm, whole garment centred with even margins. Soft even studio light on a seamless "
    "light grey background. Clean editorial streetwear catalogue, true-to-life colour, soft natural contrast, "
    "no film grain. Image 1 is a rough layout sketch; match its framing only."
)


def main(which: str = "all") -> int:
    load_dotenv(ledger.ROOT / ".env")
    print("Before:", ledger.status())
    try:
        draft, r = run("draft", DRAFT_PROMPT, tag="smoke-cf-text")
        print(f"OK  cloudflare text   {draft.name}  {r.width}x{r.height}  ~{r.units['neurons']} neurons")
        draft2, r = run("draft", REF_PROMPT, refs=[draft], tag="smoke-cf-ref")
        print(f"OK  cloudflare ref    {draft2.name}  {r.width}x{r.height}  ~{r.units['neurons']} neurons")
        if which == "all":
            final, r = run("standard", FINAL_PROMPT, refs=[draft], size="1K", tag="smoke-nb2")
            print(f"OK  gemini standard   {final.name}  {r.width}x{r.height}  ${r.cost_usd:.4f} ({r.cost_basis}) {r.units}")
    except (ProviderError, ledger.BudgetError) as e:
        print(f"FAIL {e}")
        return 1
    finally:
        print("After: ", ledger.status())
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:2]))
