import * as cheerio from 'cheerio';

// Sample a wider set to understand table patterns
const urls = [
    'https://www.troublecodes.net/honda/honda-84-85-accord-87-civic/',
    'https://www.troublecodes.net/chrysler/85-95_22-25/',
    'https://www.troublecodes.net/chrysler/87-95mpfi/',
    'https://www.troublecodes.net/audi/audi-5000/',
    'https://www.troublecodes.net/bmw/bmwcel/',
    'https://www.troublecodes.net/cadillac/91-92-brougham-5-0-vin-e-5-7l-vin-7/',
    'https://www.troublecodes.net/toyota/toyota-tercel-90-94-1-5l-3e-e/',
    'https://www.troublecodes.net/mazda/mazda-94-95-mpv-92-95-mx-3-90-95-mx-5-miata-90-94-protege-91-94-323/',
];

for (const url of urls) {
    try {
        const resp = await fetch(url);
        const html = await resp.text();
        const $ = cheerio.load(html);
        
        const content = $('article .entry-content').first();
        console.log(`\n${'='.repeat(70)}`);
        console.log(`URL: ${url.replace('https://www.troublecodes.net/', '')}`);
        
        content.find('table').each((ti, table) => {
            const $t = $(table);
            const rows = $t.find('tr');
            console.log(`\n  Table ${ti}: ${rows.length} rows`);
            
            // Show first 5 rows to understand structure
            rows.each((ri, row) => {
                if (ri > 6) return;
                const cells = [];
                $(row).find('th, td').each((_, cell) => {
                    const tag = cell.tagName;
                    const text = $(cell).text().trim().substring(0, 50);
                    const colspan = $(cell).attr('colspan');
                    cells.push(`<${tag}${colspan ? ` colspan=${colspan}` : ''}>${text}`);
                });
                console.log(`    Row ${ri}: ${cells.join(' | ')}`);
            });
            if (rows.length > 7) console.log(`    ... (${rows.length - 7} more rows)`);
        });
        
        // Also show procedure sections
        const procedures = [];
        content.find('h2, h3').each((_, el) => {
            const text = $(el).text().trim();
            if (/access|clear|reset|diagnos|retriev|erase|read|locate|procedure|test|general/i.test(text)) {
                // Get next siblings until next heading
                let content_text = '';
                let $next = $(el).next();
                while ($next.length && !$next.is('h2, h3, table')) {
                    content_text += $next.text().trim().substring(0, 100) + ' ';
                    $next = $next.next();
                }
                procedures.push({ heading: text, preview: content_text.substring(0, 150) });
            }
        });
        if (procedures.length > 0) {
            console.log(`\n  Procedures found:`);
            for (const p of procedures) {
                console.log(`    [${p.heading}] ${p.preview.substring(0, 120)}...`);
            }
        }
    } catch (e) {
        console.log(`\nERROR ${url}: ${e.message}`);
    }
}
