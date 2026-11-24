// Lightbox for OpenCode 2: registers the bundled skills (the same skills/ folder the Claude Code plugin uses).
import { existsSync, readdirSync, readFileSync } from "node:fs"
import { dirname, join, resolve } from "node:path"
import { fileURLToPath } from "node:url"

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..")
const skillsDir = join(root, "skills")

/** `{name, description, body}` of a SKILL.md; the front matter holds only those two keys. */
function parseSkill(text) {
  const m = /^---\r?\n([\s\S]*?)\r?\n---\r?\n?([\s\S]*)$/.exec(text)
  if (!m) return { name: "", description: "", body: text }
  const field = (key) => {
    const line = new RegExp(`^${key}:\\s*(.*)$`, "m").exec(m[1])
    return line ? line[1].trim().replace(/^["']|["']$/g, "") : ""
  }
  return { name: field("name"), description: field("description"), body: m[2] }
}

function loadSkills() {
  const out = []
  if (!existsSync(skillsDir)) return out
  for (const entry of readdirSync(skillsDir, { withFileTypes: true })) {
    const path = join(skillsDir, entry.name, "SKILL.md")
    if (!entry.isDirectory() || !existsSync(path)) continue
    const { name, description, body } = parseSkill(readFileSync(path, "utf8"))
    if (!description) continue
    // Skills name bundled files relative to the plugin root, so say where that is.
    const content = `Plugin root (resolve bundled paths such as scripts/, references/, templates/ from here): ${root}\n\n${body}`
    out.push({ id: entry.name, name: name || entry.name, description, path, content })
  }
  return out
}

export default {
  id: "lightbox",
  async setup(ctx) {
    if (ctx.options?.skills === false) return
    const skills = loadSkills()
    const registration = await ctx.skill.transform((editor) => {
      for (const skill of skills) {
        if (editor.get(skill.id)) continue // a skill the user already defined wins
        editor.add({ ...skill, autoinvoke: true })
      }
    })
    return () => void registration.dispose()
  },
}
