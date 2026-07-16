// Custom feeds table helper functions
// Extracted from templates/v3/base.html so browsers cache it as a static asset.
        function addCustomFeedRow(fieldId, fullKey, maxItems, pluginId) {
            const tbody = document.getElementById(fieldId + '_tbody');
            if (!tbody) return;
            
            const currentRows = tbody.querySelectorAll('.custom-feed-row');
            if (currentRows.length >= maxItems) {
                alert(`Maximum ${maxItems} feeds allowed`);
                return;
            }
            
            const newIndex = currentRows.length;
            const newRow = document.createElement('tr');
            newRow.className = 'custom-feed-row';
            newRow.setAttribute('data-index', newIndex);
            
            // Create name cell
            const nameCell = document.createElement('td');
            nameCell.className = 'px-4 py-3 whitespace-nowrap';
            const nameInput = document.createElement('input');
            nameInput.type = 'text';
            nameInput.name = `${fullKey}.${newIndex}.name`;
            nameInput.value = '';
            nameInput.className = 'block w-full px-2 py-1 border border-gray-300 rounded text-sm';
            nameInput.placeholder = 'Feed Name';
            nameInput.required = true;
            nameCell.appendChild(nameInput);
            
            // Create URL cell
            const urlCell = document.createElement('td');
            urlCell.className = 'px-4 py-3 whitespace-nowrap';
            const urlInput = document.createElement('input');
            urlInput.type = 'url';
            urlInput.name = `${fullKey}.${newIndex}.url`;
            urlInput.value = '';
            urlInput.className = 'block w-full px-2 py-1 border border-gray-300 rounded text-sm';
            urlInput.placeholder = 'https://example.com/feed';
            urlInput.required = true;
            urlCell.appendChild(urlInput);
            
            // Create logo cell
            const logoCell = document.createElement('td');
            logoCell.className = 'px-4 py-3 whitespace-nowrap';
            const logoContainer = document.createElement('div');
            logoContainer.className = 'flex items-center space-x-2';
            
            const fileInput = document.createElement('input');
            fileInput.type = 'file';
            fileInput.id = `${fieldId}_logo_${newIndex}`;
            fileInput.accept = 'image/png,image/jpeg,image/bmp,image/gif';
            fileInput.style.display = 'none';
            fileInput.dataset.index = String(newIndex);
            // Use addEventListener with dataset index to allow reindexing
            fileInput.addEventListener('change', function(e) {
                const idx = parseInt(e.target.dataset.index || '0', 10);
                handleCustomFeedLogoUpload(e, fieldId, idx, pluginId, fullKey);
            });
            
            const uploadButton = document.createElement('button');
            uploadButton.type = 'button';
            uploadButton.className = 'px-2 py-1 text-xs bg-gray-200 hover:bg-gray-300 rounded';
            // Use fileInput directly instead of getElementById for reindexing compatibility
            uploadButton.addEventListener('click', function() {
                fileInput.click();
            });
            const uploadIcon = document.createElement('i');
            uploadIcon.className = 'fas fa-upload mr-1';
            uploadButton.appendChild(uploadIcon);
            uploadButton.appendChild(document.createTextNode(' Upload'));
            
            const noLogoSpan = document.createElement('span');
            noLogoSpan.className = 'text-xs text-gray-400';
            noLogoSpan.textContent = 'No logo';
            
            logoContainer.appendChild(fileInput);
            logoContainer.appendChild(uploadButton);
            logoContainer.appendChild(noLogoSpan);
            logoCell.appendChild(logoContainer);
            
            // Create enabled cell
            const enabledCell = document.createElement('td');
            enabledCell.className = 'px-4 py-3 whitespace-nowrap text-center';
            const enabledInput = document.createElement('input');
            enabledInput.type = 'checkbox';
            enabledInput.name = `${fullKey}.${newIndex}.enabled`;
            enabledInput.checked = true;
            enabledInput.value = 'true';
            enabledInput.className = 'h-4 w-4 text-blue-600';
            enabledCell.appendChild(enabledInput);
            
            // Create remove cell
            const removeCell = document.createElement('td');
            removeCell.className = 'px-4 py-3 whitespace-nowrap text-center';
            const removeButton = document.createElement('button');
            removeButton.type = 'button';
            removeButton.className = 'text-red-600 hover:text-red-800 px-2 py-1';
            // Use addEventListener instead of string-based onclick to prevent injection
            removeButton.addEventListener('click', function() {
                removeCustomFeedRow(this);
            });
            const removeIcon = document.createElement('i');
            removeIcon.className = 'fas fa-trash';
            removeButton.appendChild(removeIcon);
            removeCell.appendChild(removeButton);
            
            // Append all cells to row
            newRow.appendChild(nameCell);
            newRow.appendChild(urlCell);
            newRow.appendChild(logoCell);
            newRow.appendChild(enabledCell);
            newRow.appendChild(removeCell);
            tbody.appendChild(newRow);
        }
        
        function removeCustomFeedRow(button) {
            const row = button.closest('tr');
            if (!row) return;
            
            if (confirm('Remove this feed?')) {
                const tbody = row.parentElement;
                if (!tbody) return;
                
                row.remove();
                
                // Re-index remaining rows
                const rows = tbody.querySelectorAll('.custom-feed-row');
                rows.forEach((r, index) => {
                    const oldIndex = r.getAttribute('data-index');
                    r.setAttribute('data-index', index);
                    // Update all input names with new index
                    r.querySelectorAll('input, button').forEach(input => {
                        const name = input.getAttribute('name');
                        if (name) {
                            // Replace pattern like "feeds.custom_feeds.0.name" with "feeds.custom_feeds.1.name"
                            input.setAttribute('name', name.replace(/\.\d+\./, `.${index}.`));
                        }
                        const id = input.id;
                        if (id) {
                            // Keep IDs aligned after reindex (supports both _logo_<n> and _logo_preview_<n>)
                            input.id = id
                                .replace(/_logo_preview_\d+$/, `_logo_preview_${index}`)
                                .replace(/_logo_\d+$/, `_logo_${index}`);
                        }
                        // Keep dataset index aligned so event handlers remain correct after reindex
                        if (input.dataset && 'index' in input.dataset) {
                            input.dataset.index = String(index);
                        }
                    });
                });
            }
        }
        
        function handleCustomFeedLogoUpload(event, fieldId, index, pluginId, fullKey) {
            const file = event.target.files[0];
            if (!file) return;
            
            const formData = new FormData();
            formData.append('file', file);
            formData.append('plugin_id', pluginId);
            
            fetch('/api/v3/plugins/assets/upload', {
                method: 'POST',
                body: formData
            })
            .then(response => {
                // Check HTTP status before parsing JSON
                if (!response.ok) {
                    return response.text().then(text => {
                        throw new Error(`Upload failed: ${response.status} ${response.statusText}${text ? ': ' + text : ''}`);
                    });
                }
                return response.json();
            })
            .then(data => {
                if (data.status === 'success' && data.data && data.data.files && data.data.files.length > 0) {
                    const uploadedFile = data.data.files[0];
                    const row = document.querySelector(`#${fieldId}_tbody tr[data-index="${index}"]`);
                    if (row) {
                        const logoCell = row.querySelector('td:nth-child(3)');
                        const existingPathInput = logoCell.querySelector('input[name*=".logo.path"]');
                        const existingIdInput = logoCell.querySelector('input[name*=".logo.id"]');
                        const pathName = existingPathInput ? existingPathInput.name : `${fullKey}.${index}.logo.path`;
                        const idName = existingIdInput ? existingIdInput.name : `${fullKey}.${index}.logo.id`;
                        
                        // Normalize path: remove leading slashes, then add single leading slash
                        const normalizedPath = String(uploadedFile.path || '').replace(/^\/+/, '');
                        const imageSrc = '/' + normalizedPath;
                        
                        // Clear logoCell and build DOM safely to prevent XSS
                        logoCell.textContent = ''; // Clear existing content
                        
                        // Create container div
                        const container = document.createElement('div');
                        container.className = 'flex items-center space-x-2';
                        
                        // Create file input
                        const fileInput = document.createElement('input');
                        fileInput.type = 'file';
                        fileInput.id = `${fieldId}_logo_${index}`;
                        fileInput.accept = 'image/png,image/jpeg,image/bmp,image/gif';
                        fileInput.style.display = 'none';
                        fileInput.dataset.index = String(index);
                        // Use addEventListener with dataset index to allow reindexing
                        fileInput.addEventListener('change', function(e) {
                            const idx = parseInt(e.target.dataset.index || '0', 10);
                            handleCustomFeedLogoUpload(e, fieldId, idx, pluginId, fullKey);
                        });
                        
                        // Create upload button
                        const uploadButton = document.createElement('button');
                        uploadButton.type = 'button';
                        uploadButton.className = 'px-2 py-1 text-xs bg-gray-200 hover:bg-gray-300 rounded';
                        // Use fileInput directly instead of getElementById for reindexing compatibility
                        uploadButton.addEventListener('click', function() {
                            fileInput.click();
                        });
                        const uploadIcon = document.createElement('i');
                        uploadIcon.className = 'fas fa-upload mr-1';
                        uploadButton.appendChild(uploadIcon);
                        uploadButton.appendChild(document.createTextNode(' Upload'));
                        
                        // Create img element - use normalized path, set src via property to prevent XSS
                        const img = document.createElement('img');
                        img.src = imageSrc; // Use property assignment with normalized path
                        img.alt = 'Logo';
                        img.className = 'w-8 h-8 object-cover rounded border';
                        img.id = `${fieldId}_logo_preview_${index}`;
                        
                        // Create hidden input for path - set value via property to prevent XSS
                        const pathInput = document.createElement('input');
                        pathInput.type = 'hidden';
                        pathInput.name = pathName;
                        pathInput.value = imageSrc;
                        
                        // Create hidden input for id - set value via property to prevent XSS
                        const idInput = document.createElement('input');
                        idInput.type = 'hidden';
                        idInput.name = idName;
                        idInput.value = String(uploadedFile.id); // Ensure it's a string
                        
                        // Append all elements to container
                        container.appendChild(fileInput);
                        container.appendChild(uploadButton);
                        container.appendChild(img);
                        container.appendChild(pathInput);
                        container.appendChild(idInput);
                        
                        // Append container to logoCell
                        logoCell.appendChild(container);
                    }
                    // Allow re-uploading the same file (change event won't fire otherwise)
                    event.target.value = '';
                } else {
                    alert('Upload failed: ' + (data.message || 'Unknown error'));
                }
            })
            .catch(error => {
                console.error('Upload error:', error);
                alert('Upload failed: ' + error.message);
            });
        }
