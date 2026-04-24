import * as cheerio from 'cheerio';

const resp = await fetch('https://www.troublecodes.net/honda/honda-84-85-accord-87-civic/');
const html = await resp.text();
const $ = cheerio.load(html);

const content = $('article .entry-content').first();
console.log('Has .entry-content:', content.length > 0);
console.log('Children count:', content.children().length);

content.children().each((i, el) => {
    const tag = el.tagName?.toLowerCase();
    const text = $(el).text().trim().substring(0, 80);
    console.log(`  [${i}] <${tag}> "${text}"`);
});
