export const BASE_URL = 'https://www.troublecodes.net';

// All known brands on troublecodes.net (slug → display name)
export const ALL_BRANDS = {
    acura: 'Acura',
    audi: 'Audi',
    bmw: 'BMW',
    buick: 'Buick',
    cadillac: 'Cadillac',
    chevrolet: 'Chevrolet',
    chrysler: 'Chrysler',
    dodge: 'Dodge',
    ford: 'Ford',
    gm: 'GM',
    gmc: 'GMC',
    honda: 'Honda',
    hyundai: 'Hyundai',
    infiniti: 'Infiniti',
    jeep: 'Jeep',
    kia: 'Kia',
    lexus: 'Lexus',
    mazda: 'Mazda',
    mb: 'Mercedes-Benz',
    nissan: 'Nissan',
    pontiac: 'Pontiac',
    saturn: 'Saturn',
    subaru: 'Subaru',
    toyota: 'Toyota',
    volvo: 'Volvo',
    vw: 'Volkswagen',
};

// Generic OBD-II code categories
export const GENERIC_CODE_PAGES = {
    pcodes: 'Powertrain',
    bcodes: 'Body',
    ccodes: 'Chassis',
    ucodes: 'Network',
};

// Route labels for the crawler
export const LABELS = {
    BRAND_CODES: 'BRAND_CODES',
    CODE_DETAIL: 'CODE_DETAIL',
    GENERIC_CODES: 'GENERIC_CODES',
    BRAND_HOME: 'BRAND_HOME',
    SUBMODEL: 'SUBMODEL',
};
