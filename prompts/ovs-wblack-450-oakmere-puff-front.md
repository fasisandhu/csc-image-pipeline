# ovs-wblack-450-oakmere-puff-front

Tier **premium**, 2K, 4:5

## Nano Banana prompt (227 words)

Front-view packshot showing a 3D puff embroidered centre-chest logo, as a ghost-mannequin catalogue shot. The garment is an oversized pullover hoodie in 450 GSM cotton-rich fleece: oversized boxy fit, dropped shoulder, wide body, slightly cropped length. The 450 GSM fabric stands away from the body, with thick rib and a hood that keeps its shape. The colour is Washed Black (#2F2F31). Construction: double-layer hood with centre seam; flat drawcords with metal aglets, through metal eyelets; kangaroo pocket, openings symmetrical, attached at the hem; 2x2 rib cuffs and hem, distinct from the body fabric; and coverstitched seams. At the centre chest, about 22 cm wide, is 3D puff embroidery of the logo shown in Image 2, reproduced exactly, reading "OAKMERE ATHLETIC" spelled exactly. Clearly visible: the raised 3D puff logo, and both drawcords with metal aglets. The camera is straight on at chest height, with a 50mm lens, whole garment centred with even margins on all sides. Soft, even studio light from a large overhead softbox with gentle front fill, showing the raised embroidery. Seamless light grey studio background with a soft natural shadow below the hem. Style: clean editorial streetwear catalogue, true-to-life colour, soft natural contrast, no film grain. Image 1 is the exact garment; match colour, rib width, drawcords and hood shape. Image 2 is the logo artwork; reproduce its shapes, colours and any text exactly.

## FLUX.2 klein prompt

```json
{
  "subject": "Front-view packshot showing a 3D puff embroidered centre-chest logo, as a ghost-mannequin catalogue shot",
  "garment": "oversized pullover hoodie, 450 GSM cotton-rich fleece, oversized boxy fit, dropped shoulder, wide body, slightly cropped length; fabric stands away from the body, with thick rib and a hood that keeps its shape; colour Washed Black (#2F2F31); double-layer hood with centre seam; flat drawcords with metal aglets, through metal eyelets; kangaroo pocket, openings symmetrical, attached at the hem; 2x2 rib cuffs and hem, distinct from the body fabric; coverstitched seams",
  "decoration": "At the centre chest, about 22 cm wide, is 3D puff embroidery of the logo shown in Image 2, reproduced exactly, reading \"OAKMERE ATHLETIC\" spelled exactly.",
  "scene": "seamless light grey studio background with a soft natural shadow below the hem",
  "camera": "straight on at chest height, 50mm, whole garment centred with even margins on all sides",
  "lighting": "soft, even studio light from a large overhead softbox with gentle front fill, showing the raised embroidery",
  "style": "clean editorial streetwear catalogue, true-to-life colour, soft natural contrast, no film grain"
}
```

## References

1. `refs/anchors/pullover-oversized_washed-black_450_front.png`: the exact garment; match colour, rib width, drawcords and hood shape
2. `brand/designs/png/oakmere-athletic_ref.png`: the logo artwork; reproduce its shapes, colours and any text exactly

## QA checklist

- [ ] Two drawcords, equal length, metal aglets, through eyelets
- [ ] Rib cuffs and hem consistent width, distinct from the body
- [ ] Shoulder seam matches the fit (dropped)
- [ ] Kangaroo pocket openings symmetrical, attached at the hem
- [ ] Hood double-layer with centre seam
- [ ] Drape matches 450 GSM: stands away from the body, with thick rib and a hood that keeps its shape
- [ ] Colour matches Washed Black #2F2F31 (delta E <= 5)
- [ ] No unintended text or logos
- [ ] Intended logo passes verification (OCR, outline, colour)
- [ ] Shows: the raised 3D puff logo
- [ ] Shows: both drawcords with metal aglets
- [ ] Free of: any other text or logos

## Negatives

- any other text or logos
- unintended text, letters or logos
- extra, missing or uneven drawcords
- extra or fused fingers
- thin T-shirt-like fabric on heavy GSM

## Warnings

- catalogue pullover-oversized: unconfirmed fit wording
- paragraph prompt is 227 words, outside 120-220
