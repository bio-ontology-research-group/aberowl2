document.addEventListener('DOMContentLoaded', () => {
    // Server list elements
    const tableBody = document.getElementById('serversTableBody');
    const searchInput = document.getElementById('searchInput');
    const sortableHeaders = document.querySelectorAll('.sortable');

    // Query UI elements
    const dlQueryForm = document.getElementById('dlQueryForm');
    const dlQueryInput = document.getElementById('dlQueryInput');
    const textSearchForm = document.getElementById('textSearchForm');
    const textSearchInput = document.getElementById('textSearchInput');
    const queryExampleLinks = document.querySelectorAll('.query-example');

    // Ontology filter elements
    const ontologyFilterCheckboxes = document.getElementById('ontologyFilterCheckboxes');

    // Results display elements
    const resultsContainer = document.getElementById('resultsContainer');
    const resultsList = document.getElementById('resultsList');
    const resultsCount = document.getElementById('resultsCount');
    const resultsFilterInput = document.getElementById('resultsFilterInput');
    const paginationContainer = document.getElementById('paginationContainer');

    // State variables
    let serversData = [];
    let sortState = {
        column: 'ontology',
        direction: 'asc'
    };
    let allResults = [];
    let currentPage = 1;
    const pageSize = 50;

    function renderTable(data) {
        tableBody.innerHTML = '';
        if (data.length === 0) {
            tableBody.innerHTML = '<tr><td colspan="7" class="text-center">No servers found.</td></tr>';
            return;
        }

        data.forEach(server => {
            const row = document.createElement('tr');
            const description = server.description || (server.submission ? server.submission.description : '') || '';
            row.innerHTML = `
                <td>${server.ontology || 'N/A'}</td>
                <td>${server.title || ''}</td>
                <td>${description}</td>
                <td>${server.class_count || 'N/A'}</td>
                <td>${server.property_count || 'N/A'}</td>
                <td><span class="label label-${server.status === 'online' ? 'success' : 'danger'}">${server.status || 'unknown'}</span></td>
                <td><a href="${server.url}" target="_blank">${server.url}</a></td>
            `;
            tableBody.appendChild(row);
        });
    }

    function sortData(data, column, direction) {
        return [...data].sort((a, b) => {
            let valA = a[column] || '';
            let valB = b[column] || '';

            // Handle numeric sorting for counts
            if (column.includes('_count')) {
                valA = parseInt(valA, 10) || 0;
                valB = parseInt(valB, 10) || 0;
            }

            if (typeof valA === 'string') {
                valA = valA.toLowerCase();
                valB = valB.toLowerCase();
            }

            if (valA < valB) {
                return direction === 'asc' ? -1 : 1;
            }
            if (valA > valB) {
                return direction === 'asc' ? 1 : -1;
            }
            return 0;
        });
    }

    function filterData(data, searchTerm) {
        const term = searchTerm.toLowerCase();
        if (!term) {
            return data;
        }
        return data.filter(server => {
            const description = server.description || (server.submission ? server.submission.description : '') || '';
            return (
                (server.ontology && server.ontology.toLowerCase().includes(term)) ||
                (server.title && server.title.toLowerCase().includes(term)) ||
                (description && description.toLowerCase().includes(term)) ||
                (server.url && server.url.toLowerCase().includes(term))
            );
        });
    }

    function updateTable() {
        const searchTerm = searchInput.value;
        const filtered = filterData(serversData, searchTerm);
        const sorted = sortData(filtered, sortState.column, sortState.direction);
        renderTable(sorted);
        populateOntologyFilter();
    }

    function updateSortIcons() {
        sortableHeaders.forEach(header => {
            const sortKey = header.dataset.sort;
            const icon = header.querySelector('i');
            icon.className = 'glyphicon glyphicon-sort'; // Reset icon
            header.classList.remove('asc', 'desc');

            if (sortKey === sortState.column) {
                if (sortState.direction === 'asc') {
                    icon.className = 'glyphicon glyphicon-sort-by-attributes';
                    header.classList.add('asc');
                } else {
                    icon.className = 'glyphicon glyphicon-sort-by-attributes-alt';
                    header.classList.add('desc');
                }
            }
        });
    }

    sortableHeaders.forEach(header => {
        header.addEventListener('click', () => {
            const column = header.dataset.sort;
            if (sortState.column === column) {
                sortState.direction = sortState.direction === 'asc' ? 'desc' : 'asc';
            } else {
                sortState.column = column;
                sortState.direction = 'asc';
            }
            updateSortIcons();
            updateTable();
        });
    });

    searchInput.addEventListener('input', updateTable);

    async function fetchServers() {
        try {
            const response = await fetch('/api/servers');
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            serversData = await response.json();
            updateSortIcons();
            updateTable();
        } catch (error) {
            console.error('Error fetching server data:', error);
            tableBody.innerHTML = `<tr><td colspan="6" class="text-center text-danger">Error loading data.</td></tr>`;
        }
    }

    // Initial fetch and render
    fetchServers();
    // Refresh data every 15 seconds
    setInterval(fetchServers, 15000);

    // Set default query value for DL query
    dlQueryInput.value = "'has part' some nucleus";

    queryExampleLinks.forEach(link => {
        link.addEventListener('click', (event) => {
            event.preventDefault();
            dlQueryInput.value = event.target.dataset.query;
        });
    });

    // Use event delegation for ontology filter checkboxes
    ontologyFilterCheckboxes.addEventListener('change', (event) => {
        if (event.target.type === 'checkbox') {
            currentPage = 1;
            renderResults();
        }
    });

    function populateOntologyFilter() {
        const ontologyMap = new Map();
        serversData.forEach(s => {
            if (s.ontology && !ontologyMap.has(s.ontology)) {
                ontologyMap.set(s.ontology, s.title || s.ontology);
            }
        });

        const uniqueOntologies = Array.from(ontologyMap.entries())
            .map(([id, title]) => ({ id, title }))
            .sort((a, b) => a.title.localeCompare(b.title));

        const checkedOntologies = new Set(
            Array.from(ontologyFilterCheckboxes.querySelectorAll('input:checked')).map(cb => cb.value)
        );

        ontologyFilterCheckboxes.innerHTML = '';
        uniqueOntologies.forEach(ontology => {
            const isFirstLoad = checkedOntologies.size === 0;
            const isChecked = isFirstLoad || checkedOntologies.has(ontology.id);
            const checkboxDiv = document.createElement('div');
            checkboxDiv.className = 'checkbox';
            checkboxDiv.innerHTML = `
                <label>
                    <input type="checkbox" value="${ontology.id}" ${isChecked ? 'checked' : ''}>
                    ${ontology.title} (${ontology.id})
                </label>
            `;
            ontologyFilterCheckboxes.appendChild(checkboxDiv);
        });
    }

    resultsFilterInput.addEventListener('input', () => {
        currentPage = 1;
        renderResults();
    });

    async function executeQuery(endpoint, params) {
        resultsList.innerHTML = '<li class="list-group-item">Loading...</li>';
        resultsContainer.style.display = 'block';
        resultsCount.textContent = '0';

        const selectedOntologies = Array.from(ontologyFilterCheckboxes.querySelectorAll('input:checked')).map(cb => cb.value);
        if (selectedOntologies.length === 0) {
            allResults = [];
            renderResults();
            return;
        }
        params.append('ontologies', selectedOntologies.join(','));

        try {
            const response = await fetch(`${endpoint}?${params}`);
            if (!response.ok) {
                const errorText = await response.text();
                throw new Error(`HTTP error! status: ${response.status}, message: ${errorText}`);
            }
            const data = await response.json();
            allResults = data.result || [];
            currentPage = 1;
            renderResults();
        } catch (error) {
            console.error('Error executing query:', error);
            resultsList.innerHTML = `<li class="list-group-item list-group-item-danger">Error: ${error.message}</li>`;
            resultsCount.textContent = '0';
        }
    }

    dlQueryForm.addEventListener('submit', async (event) => {
        event.preventDefault();
        const queryType = event.submitter.value;
        const query = dlQueryInput.value.trim();

        if (!query) {
            alert('Please enter a DL query.');
            return;
        }

        const params = new URLSearchParams({ query, type: queryType });
        executeQuery('/api/dlquery_all', params);
    });

    textSearchForm.addEventListener('submit', async (event) => {
        event.preventDefault();
        const query = textSearchInput.value.trim();

        if (!query) {
            alert('Please enter a search term.');
            return;
        }

        const params = new URLSearchParams({ query });
        executeQuery('/api/search_all', params);
    });

    function renderResults() {
        const filterText = resultsFilterInput.value.toLowerCase();
        const selectedOntologies = Array.from(ontologyFilterCheckboxes.querySelectorAll('input:checked')).map(cb => cb.value);

        const filteredResults = allResults.filter(item => {
            const label = String(item.label || item.owlClass || item.iri || '').toLowerCase();
            const ontology = item.ontology || '';
            const textMatch = !filterText || label.includes(filterText);
            const ontologyMatch = selectedOntologies.includes(ontology);
            return textMatch && ontologyMatch;
        });

        resultsCount.textContent = filteredResults.length;
        resultsList.innerHTML = '';

        if (filteredResults.length === 0) {
            resultsList.innerHTML = '<li class="list-group-item">No results found.</li>';
            renderPagination(0, 1);
            return;
        }

        const totalPages = Math.ceil(filteredResults.length / pageSize);
        const start = (currentPage - 1) * pageSize;
        const end = start + pageSize;
        const paginatedResults = filteredResults.slice(start, end);

        paginatedResults.forEach(item => {
            const li = document.createElement('li');
            li.className = 'list-group-item';

            let primaryLabel;
            let synonyms = [];
            if (Array.isArray(item.label) && item.label.length > 0) {
                primaryLabel = item.label[0];
                synonyms = item.label.slice(1);
            } else {
                primaryLabel = item.label || item.owlClass || item.iri;
            }

            const owlClass = item.owlClass || item.iri;
            const ontology = item.ontology || 'Unknown';
            const ontologyTitle = item.ontology_title || ontology;
            const server = serversData.find(s => s.ontology === ontology);

            let link;
            if (server && owlClass) {
                const browseUrl = `${server.url}#/Browse/${encodeURIComponent(owlClass)}`;
                link = `<a href="${browseUrl}" target="_blank">${primaryLabel}</a>`;
            } else {
                link = primaryLabel;
            }

            if (synonyms.length > 0) {
                link += ` <small class="text-muted">(${synonyms.join(', ')})</small>`;
            }

            li.innerHTML = `${link} <span class="label label-info pull-right" title="${ontologyTitle}">${ontologyTitle} (${ontology})</span>`;
            resultsList.appendChild(li);
        });

        renderPagination(totalPages, currentPage);
    }

    function renderPagination(totalPages, page) {
        const paginationUl = paginationContainer.querySelector('.pagination');
        paginationUl.innerHTML = '';

        if (totalPages <= 1) return;

        for (let i = 1; i <= totalPages; i++) {
            const li = document.createElement('li');
            li.className = `page-item ${i === page ? 'active' : ''}`;
            const a = document.createElement('a');
            a.className = 'page-link';
            a.href = '#';
            a.textContent = i;
            a.addEventListener('click', (e) => {
                e.preventDefault();
                currentPage = i;
                renderResults();
            });
            li.appendChild(a);
            paginationUl.appendChild(li);
        }
    }

    // Clear results when switching tabs
    document.querySelectorAll('#queryTabs a[data-toggle="tab"]').forEach(tab => {
        tab.addEventListener('shown.bs.tab', () => {
            allResults = [];
            currentPage = 1;
            renderResults();
            resultsContainer.style.display = 'none';
        });
    });
});
