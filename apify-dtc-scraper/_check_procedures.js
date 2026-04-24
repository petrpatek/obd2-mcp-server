import * as cheerio from 'cheerio';

// Check one procedure-only page in detail
const urls = [
    'https://www.troublecodes.net/ford/eectest/',
    'https://www.troublecodes.net/cadillac/91-92-allante-4-5l-vin-8/',
    'https://www.troublecodes.net/mazda/mazda-89-93-mpv-88-90-323626mx-6-88-92-929/',
];

for (const url of urls) {
    const resp = await fetch(url);
    const html = await resp.text();
    const $ = cheerio.load(html);
    const content = $('article .entry-content').first();
    
    console.log(`\n${'='.repeat(60)}`);
    console.log(url.replace('https://www.troublecodes.net/', ''));
    
    // Walk through all children and show structure
    content.children().each((i, el) => {
        const $el = $(el);
        const tag = el.tagName?.toLowerCase();
        const text = $el.text().trim().substring(0, 120);
        if (tag === 'h2' || tag === 'h3') {
            console.log(`\n  [${tag.toUpperCase()}] ${text}`);
        } else if (tag === 'ul' || tag === 'ol') {
            const items = [];
            $el.find('li').each((_, li) => items.push($(li).text().trim().substring(0, 80)));
            console.log(`  <${tag}> ${items.length} items:`);
            items.slice(0, 3).forEach(it => console.log(`    - ${it}`));
            if (items.length > 3) console.log(`    ... (${items.length - 3} more)`);
        } else if (tag === 'p') {
            if (text.length > 0) console.log(`  <p> ${text}`);
        } else if (tag === 'table') {
            const rows = $el.find('tr').length;
            console.log(`  <table> ${rows} rows`);
        }
    });
}
