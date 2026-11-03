export const meta = {
  name: 'vectorize-icons',
  description: 'Rebuild raster icons, logos or mascots as clean pixel-accurate SVGs: one builder per icon, then an independent refinement pass, then a comparison page',
  whenToUse: 'One or more icons to vectorize. args: { out: "<absolute output folder>", icons: [{ key: "blob", ref: "<absolute crop or sheet path>", box?: [x0,y0,x1,y1], desc?: "what it shows" }] }',
  phases: [
    { title: 'Build', detail: 'one agent per icon: shapes, paint, verify' },
    { title: 'Refine', detail: 'fresh agent per icon fixes the largest mismatches' },
    { title: 'Assemble', detail: 'comparison page' },
  ],
}

const A = args || {}
if (!A.out || !Array.isArray(A.icons) || !A.icons.length) {
  throw new Error('Pass args: { out: "<absolute folder>", icons: [{ key, ref, box?, desc? }] }')
}
const bad = A.icons.filter(ic => !ic || !ic.key || !ic.ref || !/^[\w-]+$/.test(ic.key))
if (bad.length) throw new Error(`Each icon needs a key (letters, digits, - or _) and a ref path; bad entries: ${JSON.stringify(bad)}`)
const SEP = A.out.includes('\\') ? '\\' : '/'
const OUT = A.out.replace(/[\\/]+$/, '')

async function run(prompt, opts) {
  try {
    return await agent(prompt, { ...opts, agentType: 'facsimile:icon-vectorizer' })
  } catch (e) {
    if (!/agent ?type|not found|unknown/i.test(String(e))) throw e
    log('agent type facsimile:icon-vectorizer unavailable; using a general agent')
    return agent(prompt, opts)
  }
}

const crop = ic => `${OUT}${SEP}ref${SEP}${ic.key}.png`
const svg = ic => `${OUT}${SEP}${ic.key}.svg`
const source = ic => (ic.box
  ? `Reference: crop box ${JSON.stringify(ic.box)} (x0,y0,x1,y1, padded to include the drop shadow) of "${ic.ref}". First save that crop to "${crop(ic)}" with Pillow (create the ref folder if needed) and use the crop as the reference.`
  : `Reference: "${ic.ref}". Copy it to "${crop(ic)}" (create the ref folder if needed) unless it is already there, and use that copy.`)
const shared = ic => `${source(ic)}
${ic.desc ? `It shows: ${ic.desc}` : 'Describe to yourself every visible part before building.'}
Deliverable: "${svg(ic)}", a standalone SVG whose viewBox is the crop size; transparent outside the tile and its shadow; no <image> or data URIs; a few dozen named, editable shapes.
Keep scratch files in a subfolder scratch_${ic.key} of your session scratchpad directory (or of the system temp folder if you have none); write only the deliverable and the reference crop into "${OUT}".`

const buildPrompt = ic => `Rebuild an icon as a pixel-accurate SVG; the user wants it as close to pixel-perfect as possible. Load the facsimile:vectorize-icon skill with the Skill tool and follow it.
${shared(ic)}
Build small geometric parts from primitives and organic silhouettes with facsimile trace; facsimile smooth must exit 0; inspect every small element in a 4x render. At least 5 render/diff rounds.
Final reply (max 4 lines): path, mean_abs_diff (whole crop and tile-only box), mask IoUs, largest remaining mismatch.`

const refinePrompt = (ic, prev) => `Independent, skeptical refinement pass on "${svg(ic)}". Previous report (do not trust it; measure first): ${String(prev || '').slice(0, 600)}
Load the facsimile:vectorize-icon skill and follow its refinement section.
${shared(ic)}
Steps: render and diff; list the 5 largest error regions with the element in each; fix them (re-fit outlines with low IoU, re-sample gradient stops, fit shadow/glow blur and opacity from profiles, fix rim strokes, eye/pupil positions, small elements); keep only changes that lower the error. Run facsimile smooth and replace any stair-stepped path with a clean construction. Look at a 4x render of every small element. At least 4 rounds.
Final reply (max 4 lines): mean_abs_diff before -> after (whole and tile-only), IoUs, smooth result, largest remaining mismatch.`

const results = await pipeline(
  A.icons,
  ic => run(buildPrompt(ic), { label: `build:${ic.key}`, phase: 'Build' }),
  (built, ic) => run(refinePrompt(ic, built), { label: `refine:${ic.key}`, phase: 'Refine' }).then(refined => ({ key: ic.key, built, refined })),
)

phase('Assemble')
const pairs = A.icons.map(ic => `--pair "${svg(ic)}" "${crop(ic)}" "${ic.key}"`).join(' ')
const page = await agent(
  `Run: facsimile gallery ${pairs} --out "${OUT}${SEP}index.html" --title "Icons"
Then run: facsimile smooth ${A.icons.map(ic => `"${svg(ic)}"`).join(' ')}
Return the page path, the smooth output, and any missing file.`,
  { label: 'assemble', phase: 'Assemble' },
)

return { page: `${OUT}${SEP}index.html`, icons: results, assembled: page }
