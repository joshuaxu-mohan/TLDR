/** @type {import('tailwindcss').Config} */
export default {
  content: [
    './index.html',
    './src/**/*.{js,jsx}',
  ],
  theme: {
    extend: {
      fontFamily: {
        // Two-font system: Playfair Display for editorial headlines, DM Sans for everything else.
        // font-mono maps to system monospace for raw transcript / source description text only.
        headline: ['"Playfair Display"', 'serif'],
        body:     ['"DM Sans"', 'sans-serif'],
        label:    ['"DM Sans"', 'sans-serif'],
        mono:     ['"Courier New"', 'Courier', 'monospace'],
      },
      colors: {
        surface: '#131313',
        'surface-container-lowest': '#0E0E0E',
        'surface-container-low': '#1C1B1B',
        'surface-container': '#201F1F',
        'surface-container-high': '#2A2A2A',
        'surface-container-highest': '#353534',
        'outline-variant': '#474747',
        outline: '#919191',
        'on-surface': '#E5E2E1',
        'on-surface-variant': '#C6C6C6',
        secondary: '#00E5FF',
        'on-secondary': '#001A1F',
        error: '#FFB4AB',
      },
      borderRadius: {
        DEFAULT: '0px',
        none: '0px',
        sm: '0px',
        md: '0px',
        lg: '0px',
        xl: '0px',
        '2xl': '0px',
        '3xl': '0px',
        full: '9999px',
      },
    },
  },
  plugins: [],
}
