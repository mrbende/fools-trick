---
name: scientific-figures
description: 'How to build data-derived figures for a manuscript so each one carries a claim, shows its uncertainty, and survives a referee. Use when drawing, revising, or auditing any figure for a paper,
  report, or thesis; when a figure set "looks like matplotlib defaults", "doesn''t land", "needs error bars", or has drifted from its captions; and before submitting any figure set. Triggers on: figure,
  panel, plot, chart, caption, error bars, publication figure, paper figure, matplotlib, figure review, does this figure work.'
version: 1.0.0
metadata:
  fools:
    tags:
    - writing
    - figures
    - research
    - data-viz
    related_skills:
    - argumentative-prose
    - grounded-citations
---


# Scientific figures

A figure is an argument that happens to be made of ink. This is how to build one so the
argument survives, and how to catch the defects that pass every automated check.

Read §1–3 before drawing. Read §9 before shipping. §10 is the catalogue of defects that
have actually reached a draft, each with the method that found it, because greps found
none of them.

---

## 1. The contract, written before any code

Four lines, in a comment at the top of the builder. If you cannot write them, you do not
yet know what the figure is.

1. **The claim.** One sentence the figure must defend. Not a topic ("the dose response")
   but a claim with a verb ("the effect climbs with array size at the predicted exponent").
2. **The evidence map.** One panel, one question. *If covering a panel with your hand
   removes no evidence, merge it, replace it, or cut it.*
3. **The unit of analysis.** What is one observation — a run, a subject, a pairing, a
   seed? Write it down now, because §3 will hold you to it.
4. **The output contract.** Final printed width, type sizes at that width, formats.

The highest rule: **the chart serves the logic.** Polish is subordinate to making the
claim clear, defensible, and checkable.

## 2. Choose the encoding by the size of the effect

This is the step most figure advice skips, and it is where the most expensive failures
live. Before choosing a chart type, compute the effect in the units the encoding will use.

> A study reported spatial segregation as a mean domain length of **3.08 cells against a
> chance of 1.99**. The obvious drawing is a raster of the field over time. Three such
> rasters were drawn and are **indistinguishable by eye**, because at two hundred cells a
> one-cell difference in mean run length is below the perceptual resolution of the
> encoding. Seventy-five percent of the ink carried none of the claim, and a line chart
> underneath did all the work.

Rules that follow:

- **Ask what one pixel, one bar-width, or one marker means in data units**, and whether
  the effect exceeds it. If it does not, change the encoding.
- A raw field shows a **large** effect. A **derived** quantity — a deviation from chance,
  a density, a residual — shows a small one.
- Put the effect **beside a construction whose answer is known**. A reading of 1.90 means
  nothing; 1.90 against an identically-built control reading 0.88 is the whole argument.
- When an effect is defined **within** matched units, draw the paired difference, not two
  marginal distributions that overlap.

## 3. A point without an interval is a decoration

The rule: **every mark that represents an estimate carries its uncertainty, or the figure
says why it does not.** Applied honestly, this is harder than it sounds.

- **Check the interval exists before you draw one.** If the artifact has no interval,
  either compute it in the analysis layer, or draw no interval and say so. There is no
  third option.
- **Never carry one series' error onto another.** Copying an arm's half-width to its
  neighbour because "the precision is the same order" is fabricating data. It is invisible
  once it is a line on a page. If two arms need intervals, compute two.
- **A filled band between two series reads as a confidence region.** If it is decorative,
  it is a lie. Remove it or restyle it so it cannot be mistaken.
- **Match the interval to the unit of analysis you wrote in §1.** A run-level standard
  error over thousands of pseudo-replicates is not the interval your design earned. This
  is the single most common way a figure asserts something the paper cannot defend.
- **Draw a null as a width, never as the absence of a mark.** "n.s." beside an interval
  that contains zero is a measurement; a missing bar is not.
- **When n is small, draw the units.** Four points beaten into a mean with a band is worse
  than four points. Show them.
- **Say what the interval is**, in the panel or the caption: 95% CI, ±1 s.e.m., IQR,
  bootstrap over which unit, and n.

Devices that reviewers reward, all of which are ways of putting the ruler in the picture:

| device | what it does |
|---|---|
| the minimum detectable effect drawn beside the estimate | makes a null interpretable |
| a constructed control on the same axis as the effect | shows the effect is real *and* small, without hedging |
| the "if it scaled" reference line | turns a ratio into a picture |
| the excluded population drawn on the same axes | makes a filtering decision visible |
| printing your own prediction's shortfall | buys more credit than hiding it |

## 4. The panel title is the claim, and it must be computed

- A panel title is **a claim with a verb**, in the section's own words. Not "Scheduler
  independence" but "The pattern survives a deterministic scheduler."
- **Every number in a title is an f-string from the data that panel plots.** Hand-typed
  numbers in titles are where drift lives, reliably and exclusively. Where the code
  interpolates, the number stays right; where a human typed it, it goes stale.
- **Check every shape word against its own series.** *Climbs, rises, monotone, doubles,
  unchanged, flat.* Reconstruct the series before using the word. A series that dips in
  the middle is not "climbing" even if its endpoints are.
- **Check the title against the statistic, not against the memory of it.** A title saying
  "costs nothing" over a result distinguishable from zero is a title that has to change.
- Titles are the part that travels. When a title and a caption disagree, readers believe
  the title.

## 5. Geometry and type

- **Author at the printed width.** If the text block is 6.5 in, the figure is 6.5 in and
  is included at full width. Authoring wide and scaling down shrinks your own type: a set
  authored at 13 in and included at 0.85 rendered its axis labels at **4.7 pt**.
- **The save function raises rather than rescales.** Width is not a free parameter; height
  is, and should vary with content.
- Type at final size: base 7–8 pt, panel titles ~8, ticks 7, legends 6.5–7, in-panel
  annotations 6–7. Nothing below ~6 pt.
- **A left-aligned title is not clipped to its axes.** It will silently overprint its
  neighbour. Wrap by character count against each panel's own width, and tune the width
  per layout rather than trusting a global constant.

## 6. Colour

- **Two populations being compared must be equal in perceptual weight.** If one is pale
  and one is saturated, the eye sees shapes on a background rather than two populations —
  which destroys any claim about how they are *arranged*.
- Use a colourblind-safe named palette declared once (Okabe–Ito is a safe default), and
  give every colour **one meaning across the whole set**.
- Reserve one hue for directional or signed semantics and do not spend it elsewhere.
- A continuous quantity gets a continuous scale, never bins.
- **If marks are painted from a slice of a colormap, the colourbar must be built from the
  same slice**, via one shared constant. Otherwise every mark reads off its own legend.
- **The colour channel must carry every filter the geometry carries.** Colouring by a
  quantity pooled over conditions the geometry has filtered is a confound hiding in a
  legend.
- Check grayscale.

## 7. Review the set, not the figure

Individually defensible figures can be collectively incoherent. Build a contact sheet —
every figure stacked at the same width — and look at it.

- **One colour, one job.** Count how many distinct meanings each hue carries across the
  set. Overloading survives only when the meanings are the same idea.
- **One scale for one metric**, or a panel title that says why not.
- **Never put absolute values from two different populations on one axis** without saying
  so in the panel.
- **Panel-title register should be uniform.** If ten figures carry claims and one carries
  descriptions, the one is wrong.
- **Read only the titles, in order.** The argument should be reconstructible from them.

## 8. Layout mechanics that actually bite

Learned by rendering, never by reading code.

- **Adding uncertainty moves the ink.** Every label positioned against a bare point must
  be re-placed once the interval exists. Expect three collisions per figure.
- Reserve **bands** in a schematic — marks here, annotation above, caption below — and let
  nothing cross between them.
- `annotation_clip` silently drops an annotation whose anchor falls outside the axes.
- `bbox_inches=None` does not disable tight cropping; it defers to rcParams.
- An equal-aspect axes cropped tight loses authored width, and width sets type size.
- `loc="best"` puts the legend on the tallest bar.
- Turning off minor ticks for one axis strips them from a log axis on the other.
- Symlog is **linear** below its threshold; do not label it "log below 1e-4".
- A second variable printed inside bars of a first is where labels get clipped.

## 9. Before shipping

Run in order. The first four are mechanical; the fifth is the only one that has ever
found the serious defects.

1. **One producer per file.** A figure written by two scripts will be typeset from the
   stale one. Enforce a registry: cited-but-unproduced is an error, produced-but-uncited
   is dead weight.
2. **Rebuild everything from the analysis artifacts**, deleting outputs first. A builder
   that raises fails the run; a figure that silently keeps its old bytes is the failure
   mode this prevents.
3. **Refuse rather than degrade.** If a required population is missing, raise with the
   command that fixes it. Never fall back to a different size, a pooled sample, or a
   default ordering — an empty default cannot masquerade as an answer, a plausible one can.
4. **Every caption re-read against the picture it is under.** Captions outlive the figures
   they describe. This is the most reliable defect in the whole practice: a caption saying
   "one run", "faint traces are raw samples", "48 bins", "1,400 runs" over a panel now
   showing ninety seeds as a median and IQR in forty-four bins beside twenty-five hundred.
   Budget ≤ 60 words; anything that argues rather than identifies belongs in the body.
5. **Render it and look at it, at final physical size, panel by panel.** Automated checks
   cannot see colour hierarchy, label clearance, a legend on a data point, or a schematic
   that is arithmetically false. Every defect in §10 passed every automated check that
   existed when it shipped.

## 10. The defect catalogue

Each of these reached a draft. None was caught by a grep.

| defect | how it was found |
|---|---|
| A schematic showed a swap that **un-sorted** the array, in a paper about sorting | reading the picture as arithmetic |
| Two bare series with a decorative band between them, in a figure whose entire content is an interval | asking what each mark's uncertainty was |
| One arm's error bar **copied** onto the other and described as "the same order" | asking where the second interval was computed |
| One run per arm drawn faintly, which reads as a population, and the run chosen sat above the median | asking what n was |
| A panel title quoting a run-level difference that **fails at the pairing level** (p = 0.13) | recomputing the statistic at the unit the design declares |
| A legend giving population counts beside a scatter of a sample | counting the dots |
| "Every one is a rate" over a predictor that is a reach measured against progress | checking the summary against the variable's definition |
| A verifier reporting "172 of 172 resolved" while silently dropping every ratio-shaped number | deliberately corrupting a number to see if the check fired |
| A caption describing the previous design, three times in one manuscript | reading the caption under the rendered figure |
| One hue meaning four different things across a set | building a contact sheet |
| Two oranges for two classes that must be told apart at scatter size | building a contact sheet |
| Panel titles overprinting the neighbouring panel | rendering, because left-aligned titles are not clipped |

**The pattern in all of them:** every number was right, every reference resolved, no
banned construction was used, and the figure was still wrong. The check that works is
reading the claim beside the data that is supposed to support it.

## 11. Reference implementation

Keep one style module that owns geometry, palette, the panel helper, and a save that
enforces width. Keep one builder per figure and one command that runs them all and fails
loudly. Write a low-resolution twin of every figure for review.

```python
FIG_W = 6.5                      # the text block; never a free parameter

def panel(ax, letter, claim, across=3, w=None):
    """A panel title is the claim, wrapped against this panel's own width."""
    body = "\n".join(textwrap.wrap(claim, w or WRAP[across]))
    ax.set_title(f"{letter}  {body}", loc="left", fontsize=7.8, pad=5)

def save(fig, name):
    if abs(fig.get_size_inches()[0] - FIG_W) > 0.01:
        raise SystemExit(f"{name}: authored width must be {FIG_W} in; height is free.")
    fig.savefig(OUT / f"{name}.png", bbox_inches="tight", pad_inches=0.02)
    fig.savefig(LOWRES / f"{name}.png", bbox_inches="tight", dpi=70)
```
