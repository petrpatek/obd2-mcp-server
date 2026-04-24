import * as cheerio from 'cheerio';

async function main() {
    const resp = await fetch('https://www.troublecodes.net/acura/acura-3-5rl/');
    const html = await resp.text();
    console.log('HTML length:', html.length);
    console.log('Has TABLE:', html.includes('<TABLE'));
    console.log('Has table:', html.includes('<table'));
    
    // Check if it's a redirect or error
    console.log('Title:', html.match(/<title>([^<]+)/)?.[1]);
    
    // Show a snippet around table content
    const idx = html.indexOf('TABLE');
    if (idx > -1) {
        console.log('TABLE context:', html.substring(idx - 20, idx + 100));
    }
    
    const d = cheerio.load(html);
    console.log('cheerio tables:', d('table').length);
    console.log('cheerio TABLE:', d('TABLE').length);
    
    // Cheerio normalizes to lowercase, so try both
    const allEls = d('*');
    let tableCount = 0;
    allEls.each((_, el) => {
        if (el.tagName === 'table') tableCount++;
    });
    console.log('Manual table count:', tableCount);
}
main().catch(e => console.error(e));
