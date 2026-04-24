import * as cheerio from 'cheerio';
import { parseSubmodelPage } from './src/parsers.js';

// All empty submodel URLs from the QA
const urls = [
    'https://www.troublecodes.net/acura/88-5991-96vlin/',
    'https://www.troublecodes.net/audi/audi-5000/',
    'https://www.troublecodes.net/audi/audi-various-models-91-04/',
    'https://www.troublecodes.net/bmw/bmwcel/',
    'https://www.troublecodes.net/bmw/bmwscntool/',
    'https://www.troublecodes.net/cadillac/91-92-brougham-5-0-vin-e-5-7l-vin-7/',
    'https://www.troublecodes.net/cadillac/91-92-allante-4-5l-vin-8/',
    'https://www.troublecodes.net/cadillac/92-93-deville-eldorado-fleetwood-seville-4-9l-vin-b/',
    'https://www.troublecodes.net/chrysler/85-95_22-25/',
    'https://www.troublecodes.net/chrysler/87-89trk/',
    'https://www.troublecodes.net/chrysler/88colt15/',
    'https://www.troublecodes.net/chrysler/88-95_30333538/',
    'https://www.troublecodes.net/chrysler/89-95_1520/',
    'https://www.troublecodes.net/chrysler/89-95jeep/',
    'https://www.troublecodes.net/chrysler/91-92_30/',
    'https://www.troublecodes.net/chrysler/92-93colt15/',
    'https://www.troublecodes.net/chrysler/95_20X/',
    'https://www.troublecodes.net/chrysler/95_25sfi/',
    'https://www.troublecodes.net/chrysler/87-95mpfi/',
    'https://www.troublecodes.net/chrysler/ram2430/',
    'https://www.troublecodes.net/chrysler/84-95tbi/',
    'https://www.troublecodes.net/chrysler/89-90_30/',
    'https://www.troublecodes.net/chrysler/91_95_30/',
    'https://www.troublecodes.net/ford/eectest/',
    'https://www.troublecodes.net/honda/honda-84-85-accord-87-civic/',
    'https://www.troublecodes.net/honda/honda-90-93-accord-2-2l/',
    'https://www.troublecodes.net/honda/honda-92-95-civic-1-51-6l-92-95-prelude-2-22-3l/',
    'https://www.troublecodes.net/honda/honda-94-95-accord-2-2l-94-95-civic-del-sol-95-odyssey-2-2l/',
    'https://www.troublecodes.net/honda/honda-94-95-passport-3-2l/',
    'https://www.troublecodes.net/honda/honda-te/',
    'https://www.troublecodes.net/honda/honda-86-89-accord-2-0l-87-91-civiccrx-85-91-prelude-2-0l/',
    'https://www.troublecodes.net/honda/honda-94-95-passport-2-6l/',
    'https://www.troublecodes.net/honda/hondadtc/',
    'https://www.troublecodes.net/jeep/89-95jeep/',
    'https://www.troublecodes.net/mazda/mazda-89-93-mpv-88-90-323626mx-6-88-92-929/',
    'https://www.troublecodes.net/mazda/90-94-mazda-b2200-or-89-93-b2600-pickup-or-91-92-626mx-6/',
    'https://www.troublecodes.net/mazda/91MZDpup/',
    'https://www.troublecodes.net/mazda/mazda-94-95-mpv-92-95-mx-3-90-95-mx-5-miata-90-94-protege-91-94-323/',
    'https://www.troublecodes.net/mazda/94-mazda-b2300-or-b3000-or-b4000-or-navajo-4-0l/',
    'https://www.troublecodes.net/toyota/toyota-86-89-cressida-supra-86-92-3-0l/',
    'https://www.troublecodes.net/toyota/toyota-camry-celica-corolla-mr2/',
    'https://www.troublecodes.net/toyota/toyota-88-91-pickup-or-4runner/',
    'https://www.troublecodes.net/toyota/toyota-88-94-land-cruiser-4-0l/',
    'https://www.troublecodes.net/toyota/toyota-tercel-90-94-1-5l-3e-e/',
    'https://www.troublecodes.net/toyota/toyota-91-95-mr2-5s-fe-or-92-95-paseo-5e-fe/',
    'https://www.troublecodes.net/toyota/toyota-camry-cressida/',
    'https://www.troublecodes.net/toyota/toyota-91-95-previa-2-4l/',
    'https://www.troublecodes.net/toyota/toyota-92-95-pickup-or-4runner-3-0l-or-93-94-t100-3-0l/',
    'https://www.troublecodes.net/toyota/toyota-supra-93-95-3-0l-3-0l-turbo/',
    'https://www.troublecodes.net/toyota/toyota-86-4runner-or-pickup-2-4l/',
    'https://www.troublecodes.net/toyota/toyota-87-92-supra-3-0l-turbo/',
    'https://www.troublecodes.net/toyota/toyota-90-93-celica-89-92-corolla/',
    'https://www.troublecodes.net/toyota/toyota-celica-94-95-corolla-93-95/',
    'https://www.troublecodes.net/toyota/toyota-92-93-camry-camry-wagon-3-0l-3vz-fe/',
    'https://www.troublecodes.net/toyota/toyota-86-celica-86-87-corollasportmr2-87-88-pickup4runner/',
];

const categories = { codes_and_procedures: [], codes_only: [], procedures_only: [], empty: [] };

for (const url of urls) {
    try {
        const resp = await fetch(url);
        const html = await resp.text();
        const $ = cheerio.load(html);
        const content = $('article .entry-content').first();
        
        // Test current parser
        const parsed = parseSubmodelPage(html);
        
        // Count numeric codes in tables
        let numericCodes = 0;
        let fourDigitCodes = 0;
        content.find('table td').each((_, td) => {
            const text = $(td).text().trim();
            if (text.match(/^\d{1,4}$/) && !text.match(/^(19|20)\d{2}$/)) numericCodes++;
            if (text.match(/^\d{4}$/) && !text.match(/^(19|20)\d{2}$/)) fourDigitCodes++;
        });
        
        // Detect procedures
        const headings = [];
        content.find('h2, h3').each((_, el) => headings.push($(el).text().trim()));
        const hasProcedure = headings.some(h => /access|clear|reset|self.?test|diagnos|retriev|erase|read|locate|procedure|general/i.test(h));
        
        const hasCodes = parsed.dtcCodes.length > 0 || numericCodes > 5;
        const slug = url.replace('https://www.troublecodes.net/', '');
        
        const info = `${slug} — parser: ${parsed.dtcCodes.length} codes, numeric cells: ${numericCodes}, 4-digit: ${fourDigitCodes}, procedures: ${hasProcedure}`;
        
        if (hasCodes && hasProcedure) categories.codes_and_procedures.push(info);
        else if (hasCodes) categories.codes_only.push(info);
        else if (hasProcedure) categories.procedures_only.push(info);
        else categories.empty.push(info);
    } catch (e) {
        console.log(`ERROR: ${url} — ${e.message}`);
    }
}

console.log(`\n=== CODES + PROCEDURES (${categories.codes_and_procedures.length}) ===`);
categories.codes_and_procedures.forEach(i => console.log(`  ${i}`));

console.log(`\n=== CODES ONLY (${categories.codes_only.length}) ===`);
categories.codes_only.forEach(i => console.log(`  ${i}`));

console.log(`\n=== PROCEDURES ONLY (${categories.procedures_only.length}) ===`);
categories.procedures_only.forEach(i => console.log(`  ${i}`));

console.log(`\n=== TRULY EMPTY (${categories.empty.length}) ===`);
categories.empty.forEach(i => console.log(`  ${i}`));
