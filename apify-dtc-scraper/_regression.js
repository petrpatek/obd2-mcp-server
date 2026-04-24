import { parseBrandCodesPage, parseGenericCodesPage, parseSubmodelPage, parseProcedures } from './src/parsers.js';

const tests = [
    // Brand codes - should still work
    { url: 'https://www.troublecodes.net/fordcodes/', fn: (html) => parseBrandCodesPage(html, 'ford'), check: (r) => r.length > 100, name: 'Ford brand codes' },
    // Generic codes
    { url: 'https://www.troublecodes.net/pcodes/', fn: (html) => parseGenericCodesPage(html), check: (r) => r.length > 500, name: 'Generic P codes' },
    // Working submodel (Acura 3.5RL)
    { url: 'https://www.troublecodes.net/acura/acura-3-5rl/', fn: (html) => parseSubmodelPage(html), check: (r) => r.dtcCodes.length > 10, name: 'Acura 3.5RL submodel' },
    // Working submodel (Ford Explorer)  
    { url: 'https://www.troublecodes.net/ford/95-ford-explorer-4-0l/', fn: (html) => parseSubmodelPage(html), check: (r) => r.dtcCodes.length > 10, name: 'Ford Explorer submodel' },
    // Toyota submodel with procedures
    { url: 'https://www.troublecodes.net/toyota/toyota-86-89-cressida-supra-86-92-3-0l/', fn: (html) => parseSubmodelPage(html), check: (r) => r.dtcCodes.length > 15, name: 'Toyota Cressida codes' },
    { url: 'https://www.troublecodes.net/toyota/toyota-86-89-cressida-supra-86-92-3-0l/', fn: (html) => parseProcedures(html), check: (r) => r.length >= 2, name: 'Toyota Cressida procedures' },
];

let passed = 0;
for (const t of tests) {
    try {
        const resp = await fetch(t.url);
        const html = await resp.text();
        const result = t.fn(html);
        const ok = t.check(result);
        const count = Array.isArray(result) ? result.length : result.dtcCodes?.length;
        console.log(`${ok ? '✓' : '✗'} ${t.name}: ${count} items`);
        if (ok) passed++;
    } catch (e) {
        console.log(`✗ ${t.name}: ${e.message}`);
    }
}
console.log(`\n${passed}/${tests.length} regression tests passed`);
