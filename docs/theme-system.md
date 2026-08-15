# Theme system and visual release gate

The public UI has one semantic color contract in the `:root` and
`html[data-theme="light"]` blocks of `web/styles.css`. Components must consume
those tokens; they must not encode a light or dark palette locally.

## Token roles

- `--bg`, `--bg-deep`: page canvas.
- `--surface`, `--surface-solid`, `--surface-soft`, `--surface-muted`,
  `--surface-raised`, `--surface-inset`: elevation and containment.
- `--text-primary`, `--text-secondary`, `--muted`: text hierarchy.
- `--line`, `--line-strong`: decorative separators.
- `--control-border`, `--focus-ring`: interactive boundaries and keyboard focus.
- `--info-*`, `--success-*`, `--warning-*`, `--error-*`: separate
  `surface`, `text`, and `border` roles. Never reuse a surface token as text.
- `--action-*`, `--send-*`: composer actions and their state changes.

SVG icons use `currentColor`. Selected, processing, disabled, error, and
success states require a non-color cue such as a border, ring, icon, or label.

## Release gate

`tests/browser/onboarding-responsive.spec.js` exercises both themes at desktop
and 390x844. It audits final computed colors after alpha composition for the
chat landing page, a completed answer with Support references, system status,
settings, account forms, and success/warning/error states.

The gate enforces:

- normal text contrast of at least 4.5:1;
- tested control boundaries of at least 3:1;
- no horizontal overflow at either viewport;
- checked-in Firefox screenshot baselines for the light/dark status route.

Run the gate with:

```bash
npm ci
npx playwright install firefox
npx playwright test tests/browser/onboarding-responsive.spec.js
```

Intentional visual changes require local review before updating snapshots with
`--update-snapshots`. CI uploads the baselines and failure diffs for review.

## New component checklist

1. Use semantic tokens only; do not add component-local black/white RGBA colors.
2. Verify default, hover, focus-visible, selected, processing, disabled,
   loading, empty, success, warning, and error states where applicable.
3. Add the component's rendered selectors to the computed-style audit.
4. Check light/dark at desktop and 390x844, including keyboard focus.
5. Update a screenshot baseline only after inspecting the rendered image.
