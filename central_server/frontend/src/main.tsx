import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import './index.css'
import Layout from './components/Layout'
import Home from './pages/Home'
import SearchResults from './pages/SearchResults'
import OntologyPage from './pages/OntologyPage'
import ClassPage from './pages/ClassPage'
import DLQueryPage from './pages/DLQueryPage'
import SparqlPage from './pages/SparqlPage'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Home />} />
          <Route path="/search" element={<SearchResults />} />
          <Route path="/ontology/:id" element={<OntologyPage />} />
          <Route path="/ontology/:id/class/:iri" element={<ClassPage />} />
          <Route path="/dlquery" element={<DLQueryPage />} />
          <Route path="/sparql" element={<SparqlPage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  </StrictMode>,
)
