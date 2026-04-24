import * as cheerio from 'cheerio';

const urls = [
    'https://www.troublecodes.net/honda/honda-84-85-accord-87-civic/',
    'https://www.troublecodes.net/honda/honda-90-93-accord-2-2l/',
    'https://www.troublecodes.net/honda/honda-86-89-accord-2-0l-87-91-civiccrx-85-91-prelude-2-0l/',
    'https://www.troublecodes.net/honda/hondadtc/',
    'https://www.troublecodes.net/toyota/toyota-86-89-cressida-supra-86-92-3-0l/',
    'https://www.troublecodes.net/toyota/toyota-camry-celica-corolla-mr2/',
    'https://www.troublecodes.net/chrysler/85-95_22-25/',
    'https://www.troublecodes.net/chrysler/87-95mpfi/',
    'https://www.troublecodes.net/chrysler/84-95tbi/',
    'https://www.troublecodes.net/audi/audi-5000/',
    'https://www.troublecodes.net/bmw/bmwcel/',
    'https://www.troublecodes.net/ford/eectest/',
    'https://www.troublecodes.net/cadillac/91-92-brougham-5-0-vin-e-5-7l-vin-7/',
    'https://www.troublecodes.net/mazda/91MZDpup/',
];

for (const url of urls) {
    try {
        const resp = await fetch(url);
        const html = await resp.text();
        const $ = cheerio.load(html);
        
        const content = $('article .entry-content').first();
        const tables = content.find('table');
        const headings = [];
        content.find('h2, h3').each((_, el) => headings.push($(el).text().trim()));
        
        // Check for code-like content in tables
        let tableCodeCount = 0;
        let tableCells = 0;
        tables.each((_, table) => {
            const tds = $(table).find('td');
            tableCells += tds.length;
            tds.each((_, td) => {
                const text = $(td).text().trim();
                if (text.match(/^[PBCU]\w{4}/i) || text.match(/^\d{1,4}$/)) tableCodeCount++;
            });
        });
        
        // Check for procedure-like content (lists)
        const listItems = content.find('li').length;
        const paragraphs = content.find('p').length;
        
        // Check for inline codes outside tables  
        const bodyText = content.text();
        const inlineCodes = (bodyText.match(/\b[PBCU]\d{4}\b/gi) || []).length;
        
        console.log(`\n=== ${url.replace('https://www.troublecodes.net/', '')} ===`);
        console.log(`  Headings: ${headings.join(' | ')}`);
        console.log(`  Tables: ${tables.length}, cells: ${tableCells}, code-like cells: ${tableCodeCount}`);
        console.log(`  List items: ${listItems}, paragraphs: ${paragraphs}`);
        console.log(`  Inline DTC codes in text: ${inlineCodes}`);
        
        if (headings.length > 0) {
            const hasProcedure = headings.some(h => /access|clear|reset|self.?test|diagnos|retriev|erase|read|locate|procedure|test/i.test(h));
            const hasCodes = headings.some(h => /trouble.?code|fault|dtc|code.?list|code.?table/i.test(h));
            console.log(`  → Has procedure sections: ${hasProcedure}`);
            console.log(`  → Has code table sections: ${hasCodes}`);
        }
    } catch (e) {
        console.log(`\n=== ${url} === ERROR: ${e.message}`);
    }
}
