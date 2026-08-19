import { createApp } from 'vue'
import { createPinia } from 'pinia'
import PrimeVue from 'primevue/config'
import ToastService from 'primevue/toastservice'
import ConfirmationService from 'primevue/confirmationservice'
import Tooltip from 'primevue/tooltip'
import Aura from '@primevue/themes/aura'
import { definePreset } from '@primevue/themes'
import 'primeicons/primeicons.css'
import './style.css'

import App from './App.vue'
import router from './router'

// Tighten form-control padding to match .list-table's 0.8125rem row density —
// PrimeVue's Aura default was tuned for controls sitting on their own, not
// next to a densified table. Only half the fix lives here: PrimeVue hardcodes
// `font-size: 1rem` in its base component CSS rather than reading it from a
// token, so the matching font-size cut lives in style.css instead.
const preset = definePreset(Aura, {
  semantic: {
    formField: {
      paddingX: '0.625rem',
      paddingY: '0.375rem',
    },
  },
  components: {
    // Buttons inherit formField's padding tokens, but a label-carrying button
    // needs more horizontal presence than a text input does at this density.
    button: {
      root: {
        paddingX: '0.875rem',
      },
    },
  },
})

const app = createApp(App)

app.use(createPinia())
app.use(router)
app.use(PrimeVue, {
  theme: {
    preset,
    options: {
      darkModeSelector: '.dark',
    },
  },
})
// Toasts (discover/test results) and confirm dialogs (destructive actions)
// are registered globally here rather than per view, same as PrimeVue
// itself.
app.use(ToastService)
app.use(ConfirmationService)
// `v-tooltip` for hover text a native `title` cannot carry: a run comment is
// free text that can run to a paragraph and hold newlines, and the browser's
// own tooltip renders it at whatever width and delay it likes, unstyled and
// untouchable in dark mode. Registered globally like the two services above;
// a plain one-word hint stays a `title` attribute.
app.directive('tooltip', Tooltip)

app.mount('#app')
