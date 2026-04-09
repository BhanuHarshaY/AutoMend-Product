/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './src/pages/**/*.{js,ts,jsx,tsx,mdx}',
    './src/components/**/*.{js,ts,jsx,tsx,mdx}',
    './src/app/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      colors: {
        bg: '#0a0e1a',
        surface: '#111827',
        border: '#1e2d4a',
        accent: {
          red: '#e63946',
          teal: '#2ec4b6',
          blue: '#3a86ff',
          yellow: '#ffbe0b',
        }
      },
    },
  },
  plugins: [],
}