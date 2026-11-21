# Herald on Atlassian's components

The same service and API as the plain-file UI in `ui/`, rebuilt with Atlassian Design System packages
(`@atlaskit/*`) so the two can be compared side by side.

| | Address | Source |
|---|---|---|
| Plain-file UI | `http://localhost:8080/` | `ui/index.html` |
| This one | `http://localhost:8080/ads/` | `ui-ads/src/`, built into `ui/ads/` |

The built files in `ui/ads/` are served by the API as they are, so `docker compose up` needs no Node.

## Change it

```bash
npm install
```

```bash
npm run build
```

`npm run build` type-checks and writes `../ui/ads/`; reload the page. `npm run dev` gives hot reload on
`http://localhost:5173` and forwards API calls to the running service. Under single sign-on the dev
server can only read (the API refuses changes that come from another origin), so use it with
`AUTH_MODE=demo`, or build and use `/ads/`.

## What is used

- Shell: `@atlaskit/navigation-system` (top bar, sidebar) and `@atlaskit/side-nav-items`.
- Screens: `dynamic-table`, `lozenge`, `modal-dialog`, `tabs`, `select`, `textfield`, `textarea`, `toggle`,
  `flag`, `section-message`, `empty-state`, `breadcrumbs`, `avatar`, `badge`, `code`, `dropdown-menu`.
- Board: `@atlaskit/pragmatic-drag-and-drop`.
- Motion: `@atlaskit/motion` (`FadeIn`, `StaggeredEntrance`) plus the motion each component brings
  (modals, flags, tabs, menus). People who ask their system for reduced motion get none.
- Colour, space, radius, shadow, type: design tokens only (`src/styles.css` holds layout and uses `var(--ds-*)`).

## Things that are easy to trip over

- `.npmrc` sets `legacy-peer-deps`: `@compiled/vite-plugin` asks for `@compiled/react` 2, the Atlaskit
  packages bring version 1. The plugin is needed only to put Atlassian's pre-built CSS in the right order.
- `package.json` must not say `"type": "module"`: the icon files are CommonJS and their default export
  breaks under that setting.
- `@atlaskit/dropdown-menu` 22 has no default export; import `DropdownMenu` from `@atlaskit/dropdown-menu/dropdown-menu`.

## Not rebuilt here

Rule editor, runbook editor, phone and mailbox stand-ins: open the plain-file UI. Rules are read-only here
on purpose: saving from this screen would turn a rule kept in a file into one owned by the interface.
