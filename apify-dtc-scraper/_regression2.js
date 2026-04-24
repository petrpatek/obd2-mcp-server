import { parseBrandCodesPage, parseGenericCodesPage, parseSubmodelPage, parseProcedures } from './src/parsers.js';

const tests = [
    { url: 'https://www.troublecodes.net/fordcodes/', fn: (html) => parseBrandCodesPage(html, 'ford'), min: 100, name: 'Ford brand codes' },
    { url: 'https://www.troublecodes.net/pcodes/', fn: (html) => parseGenericCodesPage(html), min: 500, name: 'Generic P codes' },
    { url: 'https://www.troublecodes.net/bcodes/', fn: (html) => parseGenericCodesPage(html), min: 100, name: 'Generic B codes' },
    { url: 'https://www.troublecodes.net/toyotacodes/', fn: (html) => parseBrandCodesPage(html, 'toyota'), min: 50, name: 'Toyota brand codes' },
    { url: 'https://www.troublecodes.net/dodge/96-dodge-ram-1500-3-9l/', fn: (html) => parseSubmodelPage(html), min: 10, name: 'Dodge Ram submodel', field: 'dtcCodes' },
    { url: 'https://www.troublecodes.net/toyota/toyota-86-89-cressida-supra-86-92-3-0l/', fn: (html) => parseSubmodelPage(html), min: 15, name: 'Toyota Cressida submodel', field: 'dtcCodes' },
    { url: 'https://www.troublecodes.net/honda/honda-84-85-accord-87-civic/', fn: (html) => parseProcedures(html), min: 2, name: 'Honda procedures' },
    { url: 'https://www.troublecodes.net/cadillac/91-92-allante-4-5l-vin-8/', fn: (html) => parseSubmodelPage(html), min: 30, name: 'Cadillac Allante E-codes', field: 'dtcCodes' },
];

let passed = 0;
for (const t of tests) {
    try {
        const resp = await fetch(t.url);
        if (resp.status !== 200) { console.log('SKIP ' + t.name + ': HTTP ' + resp.status); continue; }
        const html = await resp.text();
        const result = t.fn(html);
        const count = t.field ? result[t.field].length : result.length;
        const ok = count >= t.min;
        console.log((ok ? 'PASS' : 'FAIL') + ' ' + t.name + ': ' + count + ' (min ' + t.min + ')');
        if (ok) passed++;
    } catch (e) {
        console.log('FAIL ' + t.name + ': ' + e.message);
    }
}
console.log('\n' + passed + '/' + tests.length + ' regression tests passed');
