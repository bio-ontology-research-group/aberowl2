document.addEventListener('DOMContentLoaded', () => {
    const tableBody = document.getElementById('serversTableBody');
    const searchInput = document.getElementById('searchInput');
    const sortableHeaders = document.querySelectorAll('.sortable');

    let serversData = [];
    let sortState = {
        column: 'ontology',
        direction: 'asc'
    };

    function renderTable(data) {
        tableBody.innerHTML = '';
        if (data.length === 0) {
            tableBody.innerHTML = '<tr><td colspan="6" class="text-center">No servers found.</td></tr>';
            return;
        }

        data.forEach(server => {
            const row = document.createElement('tr');
            const description = server.description || (server.submission ? server.submission.description : '') || '';
            row.innerHTML = `
                <td>${server.ontology || 'N/A'}</td>
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

    const dlQueryForm = document.getElementById('dlQueryForm');
    const dlQueryInput = document.getElementById('dlQueryInput');
    const queryExampleLinks = document.querySelectorAll('.query-example');
    const dlQueryResultsContainer = document.getElementById('dlQueryResultsContainer');
    const dlQueryResultsList = document.getElementById('dlQueryResultsList');

    // Set default query value
    dlQueryInput.value = "'has part' some nucleus";

    queryExampleLinks.forEach(link => {
        link.addEventListener('click', (event) => {
            event.preventDefault();
            dlQueryInput.value = event.target.dataset.query;
        });
    });

    dlQueryForm.addEventListener('submit', async (event) => {
        event.preventDefault();
        const queryType = event.submitter.value;
        const query = dlQueryInput.value.trim();

        if (!query) {
            alert('Please enter a DL query.');
            return;
        }

        dlQueryResultsList.innerHTML = '<li class="list-group-item">Loading...</li>';
        dlQueryResultsContainer.style.display = 'block';

        try {
            const params = new URLSearchParams({ query, type: queryType });
            const response = await fetch(`/api/dlquery_all?${params}`);
            if (!response.ok) {
                const errorText = await response.text();
                throw new Error(`HTTP error! status: ${response.status}, message: ${errorText}`);
            }
            const data = await response.json();
            renderDlQueryResults(data.result);
        } catch (error) {
            console.error('Error executing DL query:', error);
            dlQueryResultsList.innerHTML = `<li class="list-group-item list-group-item-danger">Error: ${error.message}</li>`;
        }
    });

    function renderDlQueryResults(results) {
        dlQueryResultsList.innerHTML = '';
        if (!results || results.length === 0) {
            dlQueryResultsList.innerHTML = '<li class="list-group-item">No results found.</li>';
            return;
        }

        results.forEach(item => {
            const li = document.createElement('li');
            li.className = 'list-group-item';
            // The result from groovy script has 'label', 'owlClass', and now 'ontology'
            const label = item.label || item.owlClass;
            const ontology = item.ontology || 'Unknown';
            // Link to the individual server's browse page
            const server = serversData.find(s => s.ontology === ontology);
            let link = label;
            if (server && item.owlClass) {
                const browseUrl = `${server.url}#/Browse/${encodeURIComponent(item.owlClass)}`;
                link = `<a href="${browseUrl}" target="_blank">${label}</a>`;
            }
            
            li.innerHTML = `${link} <span class="label label-info pull-right">${ontology}</span>`;
            dlQueryResultsList.appendChild(li);
        });
    }
});
