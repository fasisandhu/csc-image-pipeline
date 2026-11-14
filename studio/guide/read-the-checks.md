# Read the checks

Here is what each check means. The pipeline's QA step runs these checks. Studio does not yet run them on its own jobs, so for now look over a Studio image yourself.

- **Colour:** Difference under {{colour.tolerance}} matches; {{colour.tolerance}} to {{colour.drift_max}} is corrected automatically; over {{colour.drift_max}} is remade.
- **Logo text:** Must read the same as the artwork. The comparison ignores case, spaces and punctuation.
- **Logo size:** Within {{logo.size_low_pct}}% to {{logo.size_high_pct}}% of the size you chose.
- **Stray text:** Any text that isn't in your design is flagged.
- **Fixes:** Up to {{limits.repairs_per_render}} repairs and {{limits.regenerations_per_shot}} remakes, then a person decides.
