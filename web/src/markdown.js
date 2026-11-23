/**
 * 极小的 Markdown 渲染器：模型回复里基本都是 **粗体**、短横线列表和换行。
 *
 * 引 markdown-it/DOMPurify 也能做，但为了这点语法多两个依赖不划算；先把 HTML
 * 全部转义，再只插入自己生成的白名单标签，就不存在注入面。
 */

const ESCAPES = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }

function escapeHtml(text) {
  return text.replace(/[&<>"']/g, (char) => ESCAPES[char])
}

function inline(text) {
  return text
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>')
}

export function renderMarkdown(source) {
  if (!source) return ''

  const html = []
  let list = null
  const closeList = () => {
    if (list) {
      html.push(`</${list}>`)
      list = null
    }
  }

  for (const raw of escapeHtml(source).split('\n')) {
    const line = raw.trimEnd()

    if (!line.trim()) {
      closeList()
      continue
    }

    const heading = line.match(/^\s*#{1,4}\s+(.*)$/)
    if (heading) {
      closeList()
      html.push(`<h4>${inline(heading[1])}</h4>`)
      continue
    }

    const bullet = line.match(/^\s*[-*+]\s+(.*)$/)
    if (bullet) {
      if (list !== 'ul') {
        closeList()
        html.push('<ul>')
        list = 'ul'
      }
      html.push(`<li>${inline(bullet[1])}</li>`)
      continue
    }

    const ordered = line.match(/^\s*\d+[.)]\s+(.*)$/)
    if (ordered) {
      if (list !== 'ol') {
        closeList()
        html.push('<ol>')
        list = 'ol'
      }
      html.push(`<li>${inline(ordered[1])}</li>`)
      continue
    }

    closeList()
    html.push(`<p>${inline(line)}</p>`)
  }

  closeList()
  return html.join('')
}
