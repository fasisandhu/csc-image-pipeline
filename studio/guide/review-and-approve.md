# Review and approve

Every image you create goes to the **Review** tab. Studio checks it, fixes what it can within fixed limits, then waits for a person. Nothing is approved, exported or made into a rule without a click from one of us. Either of us can decide, and Studio records who did.

## 1. The checks run by themselves

As soon as an image is made, Studio checks it:

- **Colour:** measured against the colour you chose.
- **Stray text:** any text that is not your design or the neck label.
- **Logo:** its text, shape, colours and size, when the image has a design.
- **Garment details:** an AI looks at the drawcords, cuffs and hem, shoulder seams, pocket, hood, drape and, on a model, the hands and face.

The results show beside the image: ✓ passed, ✗ a problem, ↻ being fixed, ? please look. A box on the image marks where a check found something. Read the checks explains each one.

## 2. Fixes run by themselves, within limits

- **Colour a little off:** corrected for free, up to {{limits.colour_corrections_per_render}} times per image.
- **A detail wrong** (drawcords, cuffs, pocket, hood, hands, stray text): repaired one change at a time, up to {{limits.repairs_per_render}} repairs per image. After that, Studio makes a new image.
- **Something a repair can't fix** (colour far off, fabric too thin for its weight, framing): a new image, up to {{limits.regenerations_per_shot}} new images per shot.
- **Logo wrong:** one repair. If it still fails, the real artwork is placed on the approved reference packshot (front packshots only); otherwise it waits for you.
- **Face wrong:** while the model has no approved reference photo, Studio does not remake it by itself; it waits for you.

Every fix is checked again. The card shows the spend so far and the repairs and new images left. Open **Show what ran automatically** to see each step and its cost. The most it could cost was shown before **Approve & create**.

When the limits are used up, or a check or fix fails, Studio stops. Nothing is retried, and the image shows **Needs a person** with the reason.

## 3. Your verdict

Click an image in the list to open it. Use the small pictures under it to compare before and after.

- **Approve:** the image goes to the Library, ready to export. If it needs a person, write a short note saying why it is fine.
- **Ask for a change:** pick one thing to fix. Studio repairs this image once, as a paid edit, and checks it again. It counts toward the repairs left.
- **Change my choices:** opens Create image with this image's choices, to make a new one.
- **Reject with a note:** say what is wrong. The image is rejected and your note becomes a draft rule (step 5).
- **Run the checks:** for an older image that was never checked, or one that needs a person. No new image is made.

## 4. Reference packshots

A reference packshot is the approved plain front packshot of one style, colour and weight. Later shots of that style, colour and weight copy it as the exact garment, so colour, rib and hood match across the set.

- Only a plain front packshot, with no design and made without a sketch, shows **Approve as reference packshot**.
- If that colour already has one, Studio asks first and swaps it only when you press **Replace**.
- If you create a shot in a colour with no reference packshot, Studio asks you to make the packshot first. Approve it here, then create your shot.

## 5. Rejection notes become rules

Your rejection note waits under **Rules from rejection notes**, with a one-sentence rule the AI drafted from it.

- Edit the sentence if you like, and pick **Where it applies**: all images, packshots, on-model, or one style.
- **Switch on** adds it to every prompt in that scope from then on. Studio refuses a rule that would make the prompt too long.
- **Dismiss** drops it for good.

A rule never switches on by itself.

## 6. Export from the Library

Approved images are in the **Library** tab. Filter by style, colour, technique, placement, model, or packshot and on-model. Click one to see its record and the full prompt.

- **Export for the website** saves WebP files in up to three web sizes on this PC, with the alt text and caption to copy. It is free (no AI call) and publishes nothing.
- **Approval PDF**, for client mockups only: a one-page sheet for the client to sign.

Every exported image keeps its caption (see Start here).
