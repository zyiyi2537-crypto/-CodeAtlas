import rss from '@astrojs/rss'
import { getCollection } from 'astro:content'

export async function GET(context: { site: URL }) {
  const posts = (await getCollection('articles')).sort(
    (left, right) => right.data.publishedAt.valueOf() - left.data.publishedAt.valueOf(),
  )
  return rss({
    title: 'CodeAtlas Engineering Log',
    description: '私有代码知识库、混合检索与 MCP 工程实践。',
    site: context.site,
    items: posts.map((post) => ({
      title: post.data.title,
      description: post.data.description,
      pubDate: post.data.publishedAt,
      link: `/articles/${post.id}/`,
    })),
  })
}
