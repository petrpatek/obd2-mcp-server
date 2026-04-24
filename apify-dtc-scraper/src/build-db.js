/**
 * build-db.js
 *
 * Reads the Crawlee dataset from ./storage/datasets/dtc-database/
 * and converts it into a file-tree structured database.
 *
 * Usage:
 *   npm run build-db                          # outputs to ./dtc-db/
 *   node src/build-db.js --out ../src/obd2_mcp/dtc_db   # custom output dir
 *
 * Output structure:
 *   dtc-db/
 *   ├── index.json
 *   ├── generic/
 *   │   ├── pcodes.json        (powertrain)
 *   │   ├── bcodes.json        (body)
 *   │   ├── ccodes.json        (chassis)
 *   │   └── ucodes.json        (network)
 *   ├── ford/
 *   │   ├── index.json         (summary)
 *   │   ├── codes.json         (all manufacturer-specific codes)
 *   │   ├── submodels.json     (vehicle/year/engine groups)
 *   │   └── details/           (if detail pages were scraped)
 *   │       ├── p0171.json
 *   │       └── ...
 *   ├── toyota/
 *   │   └── ...
 *   └── ...
 */

import fs from 'fs/promises';
import path from 'path';

// ── Parse args ─────────────────────────────────────────────────────────
const args = process.argv.slice(2);
let outputDir = './dtc-db';
let datasetDir = './storage/datasets/dtc-database';

for (let i = 0; i < args.length; i++) {
    if (args[i] === '--out' && args[i + 1]) outputDir = args[++i];
    if (args[i] === '--dataset' && args[i + 1]) datasetDir = args[++i];
}

outputDir = path.resolve(outputDir);
datasetDir = path.resolve(datasetDir);

console.log(`Reading dataset from: ${datasetDir}`);
console.log(`Writing DB to:        ${outputDir}\n`);

// ── Read all dataset items ─────────────────────────────────────────────
const files = (await fs.readdir(datasetDir)).filter((f) => f.endsWith('.json'));
files.sort(); // process in order

const items = [];
for (const file of files) {
    const raw = await fs.readFile(path.join(datasetDir, file), 'utf-8');
    const parsed = JSON.parse(raw);
    // Crawlee may store single items or arrays
    if (Array.isArray(parsed)) {
        items.push(...parsed);
    } else {
        items.push(parsed);
    }
}

console.log(`Loaded ${items.length} dataset items from ${files.length} files\n`);

// ── Organize by type ───────────────────────────────────────────────────
const generic = {};      // pageSlug → { category, codes }
const brandCodes = {};    // brand → { brandName, codes }
const brandSubmodels = {}; // brand → [submodel, ...]
const brandDetails = {};   // brand → { code → detail }
const brandProcedures = {}; // brand → [procedure, ...]

for (const item of items) {
    switch (item.type) {
        case 'generic_codes':
            generic[item.pageSlug] = {
                category: item.category,
                codesCount: item.codesCount,
                codes: item.codes,
            };
            break;

        case 'brand_codes':
            brandCodes[item.brand] = {
                brandName: item.brandName,
                codesCount: item.codesCount,
                codes: item.codes,
            };
            break;

        case 'submodel':
            if (!brandSubmodels[item.brand]) brandSubmodels[item.brand] = [];
            brandSubmodels[item.brand].push({
                title: item.title,
                url: item.url,
                vehicles: item.vehicles,
                dtcCodes: item.dtcCodes,
            });
            break;

        case 'code_detail':
            if (!brandDetails[item.brand]) brandDetails[item.brand] = {};
            brandDetails[item.brand][item.code] = {
                title: item.title,
                sections: item.sections,
                fullText: item.fullText,
            };
            break;

        case 'procedure':
            if (!brandProcedures[item.brand]) brandProcedures[item.brand] = [];
            brandProcedures[item.brand].push({
                title: item.title,
                url: item.url,
                procedures: item.procedures,
            });
            break;

        default:
            console.warn(`Unknown item type: ${item.type}`);
    }
}

// ── Write file tree ────────────────────────────────────────────────────

// Generic codes
for (const [slug, data] of Object.entries(generic)) {
    await writeJSON(path.join(outputDir, 'generic', `${slug}.json`), data);
    console.log(`  generic/${slug}.json — ${data.codesCount} codes`);
}

// All known brands (union of codes + submodels + details + procedures)
const allBrands = new Set([
    ...Object.keys(brandCodes),
    ...Object.keys(brandSubmodels),
    ...Object.keys(brandDetails),
    ...Object.keys(brandProcedures),
]);

const brandSummaries = [];

for (const brand of [...allBrands].sort()) {
    const brandDir = path.join(outputDir, brand);
    const codes = brandCodes[brand]?.codes ?? [];
    const submodels = brandSubmodels[brand] ?? [];
    const details = brandDetails[brand] ?? {};
    const procedures = brandProcedures[brand] ?? [];
    const brandName = brandCodes[brand]?.brandName ?? brand;

    // codes.json
    await writeJSON(path.join(brandDir, 'codes.json'), codes);

    // submodels.json
    if (submodels.length > 0) {
        await writeJSON(path.join(brandDir, 'submodels.json'), submodels);
    }

    // procedures.json
    if (procedures.length > 0) {
        await writeJSON(path.join(brandDir, 'procedures.json'), procedures);
    }

    // details/
    for (const [code, detail] of Object.entries(details)) {
        await writeJSON(
            path.join(brandDir, 'details', `${code.toLowerCase()}.json`),
            detail
        );
    }

    // index.json (brand summary)
    const codeTypes = { P: 0, B: 0, C: 0, U: 0 };
    for (const c of codes) {
        const prefix = c.code?.[0]?.toUpperCase();
        if (prefix && prefix in codeTypes) codeTypes[prefix]++;
    }

    const brandIndex = {
        name: brandName,
        slug: brand,
        totalCodes: codes.length,
        totalSubmodels: submodels.length,
        totalDetails: Object.keys(details).length,
        totalProcedures: procedures.length,
        codeTypes,
    };

    await writeJSON(path.join(brandDir, 'index.json'), brandIndex);

    brandSummaries.push({
        slug: brand,
        name: brandName,
        totalCodes: codes.length,
        totalSubmodels: submodels.length,
        totalDetails: Object.keys(details).length,
        totalProcedures: procedures.length,
    });

    const detailCount = Object.keys(details).length;
    const procCount = procedures.length;
    console.log(
        `  ${brand}/ — ${codes.length} codes, ${submodels.length} submodels${detailCount ? `, ${detailCount} details` : ''}${procCount ? `, ${procCount} procedures` : ''}`
    );
}

// Top-level index
const topIndex = {
    scrapedAt: new Date().toISOString(),
    source: 'https://www.troublecodes.net',
    totalBrands: brandSummaries.length,
    totalBrandCodes: brandSummaries.reduce((s, b) => s + b.totalCodes, 0),
    totalGenericCodes: Object.values(generic).reduce(
        (s, g) => s + g.codesCount,
        0
    ),
    totalProcedures: brandSummaries.reduce((s, b) => s + b.totalProcedures, 0),
    brands: brandSummaries,
    generic: Object.entries(generic).map(([slug, data]) => ({
        slug,
        category: data.category,
        totalCodes: data.codesCount,
    })),
};

await writeJSON(path.join(outputDir, 'index.json'), topIndex);

console.log(`\nDone! ${topIndex.totalBrands} brands, ${topIndex.totalBrandCodes} brand-specific codes, ${topIndex.totalGenericCodes} generic codes`);
console.log(`Output: ${outputDir}`);

// ── Helpers ────────────────────────────────────────────────────────────

async function writeJSON(filePath, data) {
    await fs.mkdir(path.dirname(filePath), { recursive: true });
    await fs.writeFile(filePath, JSON.stringify(data, null, 2), 'utf-8');
}
