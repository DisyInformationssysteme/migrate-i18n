(defun test-this-python-file ()
  (interactive)
  (shell-command
   (concat (buffer-file-name (current-buffer)) " --test /home/babenhauserheide/IDEA/cadenza-trunk-i18ntorb/cadenza/Integration_Framework_Table_Edit_Desktop")))

