import { cpSync, mkdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(fileURLToPath(new URL('..', import.meta.url)));
mkdirSync(resolve(root, 'dist/public'), { recursive: true });
cpSync(resolve(root, 'public'), resolve(root, 'dist/public'), { recursive: true });
