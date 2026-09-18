/** 回答排版层：只渲染Markdown文本，不执行HTML，也不自动加载模型给出的图片。 */
import MarkdownIt from 'markdown-it'

const markdown = new MarkdownIt({ html: false, breaks: true, linkify: false }).disable('image')

/** 排版函数：模型回答和历史回答使用相同规则，危险链接由解析器过滤。 */
export function renderMarkdown(text: string): string { return markdown.render(text) }
