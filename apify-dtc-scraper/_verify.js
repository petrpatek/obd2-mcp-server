import { parseSubmodelPage, parseProcedures } from './src/parsers.js';

// Test against representative pages from each category
const testCases = [
    // Honda - numeric codes + procedures
    { url: 'https://www.troublecodes.net/honda/honda-84-85-accord-87-civic/', expectCodes: true, expectProcs: true },
    // Toyota - numeric codes + procedures
    { url: 'https://www.troublecodes.net/toyota/toyota-tercel-90-94-1-5l-3e-e/', expectCodes: true, expectProcs: true },
    // Chrysler - numeric codes, 2-cell continuation rows
    { url: 'https://www.troublecodes.net/chrysler/85-95_22-25/', expectCodes: true, expectProcs: false },
    // Chrysler MPFI - section header + numeric codes
    { url: 'https://www.troublecodes.net/chrysler/87-95mpfi/', expectCodes: true, expectProcs: false },
    // Audi 5000 - 4-digit codes, no headers
    { url: 'https://www.troublecodes.net/audi/audi-5000/', expectCodes: true, expectProcs: false },
    // BMW CEL - multi-column cross-ref, 4-digit codes
    { url: 'https://www.troublecodes.net/bmw/bmwcel/', expectCodes: true, expectProcs: false },
    // Cadillac Allante - E-codes + procedures
    { url: 'https://www.troublecodes.net/cadillac/91-92-allante-4-5l-vin-8/', expectCodes: true, expectProcs: true },
    // Cadillac Brougham - multi-table, Step/Action + code tables
    { url: 'https://www.troublecodes.net/cadillac/91-92-brougham-5-0-vin-e-5-7l-vin-7/', expectCodes: true, expectProcs: true },
    // Ford EEC Test - pure procedure, no codes
    { url: 'https://www.troublecodes.net/ford/eectest/', expectCodes: false, expectProcs: true },
    // Mazda procedure-only
    { url: 'https://www.troublecodes.net/mazda/mazda-89-93-mpv-88-90-323626mx-6-88-92-929/', expectCodes: false, expectProcs: true },
    // Chrysler 88 Colt - truly empty
    { url: 'https://www.troublecodes.net/chrysler/88colt15/', expectCodes: false, expectProcs: false },
];

let passed = 0;
let failed = 0;

for (const tc of testCases) {
    try {
        const resp = await fetch(tc.url);
        const html = await resp.text();
        
        const result = parseSubmodelPage(html);
        const procs = parseProcedures(html);
        
        const gotCodes = result.dtcCodes.length > 0;
        const gotProcs = procs.length > 0;
        
        const codesOk = tc.expectCodes === gotCodes;
        const procsOk = tc.expectProcs === gotProcs;
        
        const slug = tc.url.replace('https://www.troublecodes.net/', '');
        
        if (codesOk && procsOk) {
            passed++;
            console.log(`✓ ${slug}`);
            console.log(`    codes: ${result.dtcCodes.length}, procedures: ${procs.length}`);
            if (result.dtcCodes.length > 0) {
                const sample = result.dtcCodes.slice(0, 3).map(c => `${c.code}: ${c.faultLocation.substring(0, 40)}`);
                console.log(`    sample codes: ${sample.join(' | ')}`);
            }
            if (procs.length > 0) {
                const procTypes = procs.map(p => `${p.type}(${p.steps.length} steps)`);
                console.log(`    procedures: ${procTypes.join(', ')}`);
            }
        } else {
            failed++;
            console.log(`✗ ${slug}`);
            if (!codesOk) console.log(`    CODES: expected ${tc.expectCodes}, got ${gotCodes} (${result.dtcCodes.length})`);
            if (!procsOk) console.log(`    PROCS: expected ${tc.expectProcs}, got ${gotProcs} (${procs.length})`);
        }
    } catch (e) {
        failed++;
        console.log(`✗ ${tc.url} — ERROR: ${e.message}`);
    }
}

console.log(`\n${passed}/${passed + failed} tests passed`);
