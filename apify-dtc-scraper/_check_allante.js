import * as cheerio from 'cheerio';

const resp = await fetch('https://www.troublecodes.net/cadillac/91-92-allante-4-5l-vin-8/');
const html = await resp.text();
const $ = cheerio.load(html);

// Check the 60-row table
const tables = $('article .entry-content table');
tables.each((i, t) => {
    const rows = $(t).find('tr');
    if (rows.length < 3) return;
    console.log(`\nTable ${i}: ${rows.length} rows`);
    rows.each((ri, row) => {
        if (ri > 8) return;
        const cells = [];
        $(row).find('th, td').each((_, c) => {
            cells.push(`[${c.tagName}] ${$(c).text().trim().substring(0, 50)}`);
        });
        console.log(`  Row ${ri}: ${cells.join(' | ')}`);
    });
    if (rows.length > 9) console.log(`  ... ${rows.length - 9} more rows`);
});
