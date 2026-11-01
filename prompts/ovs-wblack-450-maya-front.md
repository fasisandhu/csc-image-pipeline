# ovs-wblack-450-maya-front

Tier **standard**, 2K, 4:5

## Nano Banana prompt (228 words)

Three-quarter-length on-model shot for the product page, showing the oversized fit and heavyweight drape. The garment is an oversized pullover hoodie in 450 GSM cotton-rich fleece: oversized boxy fit, dropped shoulder, wide body, slightly cropped length. The 450 GSM fabric stands away from the body, with thick rib and a hood that keeps its shape. The colour is Washed Black (#2F2F31). Construction: double-layer hood with centre seam; flat woven drawcords, about 1 cm wide, with metal aglets, through metal eyelets; kangaroo pocket, openings symmetrical, attached at the hem; 2x2 rib cuffs and hem, distinct from the body fabric; and coverstitched seams. The garment is plain and unbranded. Worn by a model (Black woman, early 20s, natural hair in a low puff, medium build, 168 cm), standing relaxed with weight on one leg, one hand in the kangaroo pocket, looking just off camera. The fleece shows natural creases at the elbows and around the pocket. Clearly visible: dropped shoulder seams, and both drawcords with metal aglets. The camera is at chest height, slightly to the model's right, with an 85mm lens, head to mid-thigh with the model centred. Soft window-style key light from camera left with gentle fill. Plain warm off-white studio wall. Style: clean editorial streetwear catalogue, true-to-life colour, soft natural contrast, no film grain. Image 1 is the exact garment; match colour, rib width and hood shape.

## FLUX.2 klein prompt

```json
{
  "subject": "Three-quarter-length on-model shot for the product page, showing the oversized fit and heavyweight drape; worn by Black woman, early 20s, natural hair in a low puff, medium build, 168 cm, standing relaxed with weight on one leg, one hand in the kangaroo pocket, looking just off camera",
  "garment": "oversized pullover hoodie, 450 GSM cotton-rich fleece, oversized boxy fit, dropped shoulder, wide body, slightly cropped length; fabric stands away from the body, with thick rib and a hood that keeps its shape; colour Washed Black (#2F2F31); double-layer hood with centre seam; flat woven drawcords, about 1 cm wide, with metal aglets, through metal eyelets; kangaroo pocket, openings symmetrical, attached at the hem; 2x2 rib cuffs and hem, distinct from the body fabric; coverstitched seams",
  "decoration": "The garment is plain and unbranded.",
  "scene": "plain warm off-white studio wall",
  "camera": "at chest height, slightly to the model's right, 85mm, head to mid-thigh with the model centred",
  "lighting": "soft window-style key light from camera left with gentle fill",
  "style": "clean editorial streetwear catalogue, true-to-life colour, soft natural contrast, no film grain"
}
```

## References

1. `refs/anchors/pullover-oversized_washed-black_450_front.png`: the exact garment; match colour, rib width and hood shape

## QA checklist

- [ ] Two drawcords, equal length, metal aglets, through eyelets
- [ ] Rib cuffs and hem consistent width, distinct from the body
- [ ] Shoulder seam matches the fit (dropped)
- [ ] Kangaroo pocket openings symmetrical, attached at the hem
- [ ] Hood double-layer with centre seam
- [ ] Drape matches 450 GSM: stands away from the body, with thick rib and a hood that keeps its shape
- [ ] Colour matches Washed Black #2F2F31 (delta E <= 5)
- [ ] Hands correct (five fingers, not fused into pockets)
- [ ] Face matches the cast sheet for Maya
- [ ] No unintended text or logos
- [ ] Shows: dropped shoulder seams
- [ ] Shows: both drawcords with metal aglets
- [ ] Free of: text or logos
- [ ] Free of: tucked-in hem

## Negatives

- text or logos
- tucked-in hem
- unintended text, letters or logos
- extra, missing or uneven drawcords
- extra or fused fingers
- thin T-shirt-like fabric on heavy GSM

## Warnings

- catalogue pullover-oversized: unconfirmed fit wording
- cast Maya: no approved reference sheet yet, face consistency relies on the description
- paragraph prompt is 228 words, outside 120-220
